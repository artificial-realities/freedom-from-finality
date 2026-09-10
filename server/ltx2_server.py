"""Resident LTX-2.5 inference server.

Holds the LTX-2.5 pipeline in GPU memory and serves /generate requests.
The client sends only a prompt; this server owns the sliding window that
carries the story from one chunk to the next.

Which weights, which recipe
---------------------------
`Lightricks/LTX-2.5-Diffusers` ships two transformers. `transformer/`
(what `from_pretrained` loads by default) is the **distilled** DiT and
must run unguided on the fixed `DISTILLED_SIGMA_VALUES` schedule (8
steps, one forward each). `transformer_full/` is the full / SFT DiT and
runs 30 steps with the pipeline's default guidance (which are the SFT
values) and a shifted schedule. Until 2026-09-09 this server ran the
distilled weights with the full recipe (15 linear steps, CFG 3, STG),
which the model card says will "quietly cost quality"; the "broken
distilled LoRA" was that LoRA stacked on already-distilled weights.

    LTX2_VARIANT=distilled   (default) transformer/, DISTILLED_SIGMA_VALUES, all guidance off
    LTX2_VARIANT=full        transformer_full/, 30 steps, SFT guidance defaults, dynamic shifting

Continuity model
----------------
`LTX2ConditionPipeline` takes a list of `LTX2VideoCondition(frames,
index, strength)`. A condition at latent index 0 overwrites the leading
tokens; a condition at any other index is appended as a guiding keyframe
(its own tokens, blended in by `strength` at every step). We send:

  * index 0 — the reference image on turn 1 and on a cut (`reset_window`),
    otherwise the trailing `continuity_frames` of the previous chunk at
    `continuity_strength`;
  * optionally, the reference image again as a keyframe at `anchor_index`
    (default -1, the last latent frame) with `anchor_strength`. This is
    the identity anchor: the clip has to arrive back at the sheet, so
    mid-clip drift has to be reversible. diffusers' own long-form recipe
    uses anchor keyframes at strength 0.95. 0.0 (default) = off.

`continuity_strength` is the steering knob: 1.0 holds the previous chunk
hard (and largely ignores a new prompt), lower values let the prompt
through at the cost of visual continuity.

Frames are kept in memory as uint8 [0, 255] and handed straight back as
the next condition. The pipeline maps conditions with
`pixels / 127.5 - 1.0` and re-compresses single images at the CRF the
model was trained with, so no PNG round trip is needed here.

Native audio: LTX-2.5 generates a synchronized audio track, so the MP4
this server returns already carries ambient sound and effects.

Setup on a GPU box: see server/remote_setup.sh (diffusers from main; the
LTX-2.5 pipelines are not in a release yet). The weights are gated:
accept the licence on the model page and export HF_TOKEN.

Environment variables:
    HF_TOKEN          HuggingFace token (required — the weights are gated)
    LTX2_MODEL        model id (default: Lightricks/LTX-2.5-Diffusers)
    LTX2_VARIANT      "distilled" (default) or "full"
    LTX2_STEPS        full variant only: sampling steps (default 30)
    LTX2_GUIDANCE     full variant only: override guidance_scale
    LTX2_STG          full variant only: override stg_scale
    LTX2_WIDTH        output width, multiple of 32 (default: 960)
    LTX2_HEIGHT       output height, multiple of 32 (default: 544)
    LTX2_OFFLOAD      "cpu" or "sequential" when VRAM is tight
    LTX2_VAE_TILING   "1" to enable VAE tiling (lower peak VRAM on decode)
    LTX2_HOST/PORT    bind address (default: 0.0.0.0:8000)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import tempfile
import time

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ltx2_server")

# ── Model / output defaults ───────────────────────────────────────
# NB: "Lightricks/LTX-2.5" is the ComfyUI / ltx-pipelines layout (raw
# safetensors under diffusion_models/) with no model_index.json, so
# diffusers cannot load it. "-Diffusers" is the same 22B weights in the
# layout from_pretrained() expects.
MODEL_ID = os.getenv("LTX2_MODEL", "Lightricks/LTX-2.5-Diffusers")
VARIANT = os.getenv("LTX2_VARIANT", "distilled").lower()
if VARIANT not in ("distilled", "full"):
    raise SystemExit(f"LTX2_VARIANT must be 'distilled' or 'full', got {VARIANT!r}")

# 960x544 is the resolution the model card recommends for LTX-2.5.
WIDTH = int(os.getenv("LTX2_WIDTH", "960"))
HEIGHT = int(os.getenv("LTX2_HEIGHT", "544"))
FRAME_RATE = 24.0

# ── Server state ──────────────────────────────────────────────────
app = FastAPI(title="LTX-2.5 Inference Server")
pipe = None
_encode_video = None
_negative_default: str = "blurry, out of focus, low contrast, flickering, motion blur"
_call_defaults: dict = {}   # recipe: sigmas / steps and guidance for the variant
_steps: int = 0
_audio_sample_rate: int = 48000

# Session: the reference image (PIL, seeds turn 1 and anchors) and the
# tail of the last generated chunk (uint8 ndarray, shape (F, H, W, 3)).
session: dict = {}
_lock = asyncio.Lock()


class GenerateRequest(BaseModel):
    prompt: str
    reference_image: str | None = None  # base64 PNG, required on first call
    continuity_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    continuity_frames: int = Field(default=9, ge=1)
    seed: int | None = None
    num_frames: int = Field(default=121, ge=9)
    # A cut: condition on the reference image, not the previous chunk's tail.
    reset_window: bool = False
    # Override the server's default negative prompt for this request.
    negative_prompt: str | None = None
    # Identity anchor: the reference image as a guiding keyframe at
    # `anchor_index` (latent frames; -1 = last) with this strength. 0 = off.
    anchor_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    anchor_index: int = -1


# ── Startup ───────────────────────────────────────────────────────

@app.on_event("startup")
async def load_pipeline() -> None:
    global pipe, _encode_video, _negative_default, _call_defaults, _steps
    global _audio_sample_rate

    from diffusers import LTX2ConditionPipeline
    from diffusers.pipelines.ltx2.utils import DEFAULT_NEGATIVE_PROMPT, DISTILLED_SIGMA_VALUES
    from diffusers.utils import encode_video

    _encode_video = encode_video
    _negative_default = DEFAULT_NEGATIVE_PROMPT

    if not (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")):
        logger.warning(
            "No HF_TOKEN set — %s is gated and the download will fail. "
            "Accept the licence on the model page, then export HF_TOKEN.",
            MODEL_ID,
        )

    logger.info("Loading %s (%s) …", MODEL_ID, VARIANT)
    t0 = time.monotonic()
    try:
        if VARIANT == "full":
            from diffusers import FlowMatchEulerDiscreteScheduler, LTX2VideoTransformer3DModel

            # transformer_full/ is not in model_index.json: load it explicitly.
            # Passing transformer= also keeps from_pretrained from fetching
            # the distilled folder.
            transformer = LTX2VideoTransformer3DModel.from_pretrained(
                MODEL_ID, subfolder="transformer_full", dtype=torch.bfloat16,
            )
            pipe = LTX2ConditionPipeline.from_pretrained(
                MODEL_ID, transformer=transformer, dtype=torch.bfloat16,
            )
            # The shipped scheduler is configured for the distilled schedule
            # (no shifting); the full DiT needs the shifting back on.
            pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
                pipe.scheduler.config, use_dynamic_shifting=True, shift_terminal=0.1,
            )
            _steps = int(os.getenv("LTX2_STEPS", "30"))
            # Guidance stays at the pipeline defaults (the SFT values:
            # guidance 3.0, STG 1.0, modality 3.0, rescale 0.7, audio 7.0)
            # unless overridden for an experiment.
            _call_defaults = {"num_inference_steps": _steps}
            if os.getenv("LTX2_GUIDANCE"):
                _call_defaults["guidance_scale"] = float(os.environ["LTX2_GUIDANCE"])
            if os.getenv("LTX2_STG"):
                _call_defaults["stg_scale"] = float(os.environ["LTX2_STG"])
        else:
            pipe = LTX2ConditionPipeline.from_pretrained(MODEL_ID, dtype=torch.bfloat16)
            # Distilled recipe from the model card: the fixed sigma schedule,
            # unguided, STG and modality guidance off, one forward per step.
            _steps = len(DISTILLED_SIGMA_VALUES)
            _call_defaults = {
                "sigmas": list(DISTILLED_SIGMA_VALUES),
                "guidance_scale": 1.0,
                "audio_guidance_scale": 1.0,
                "stg_scale": 0.0,
                "audio_stg_scale": 0.0,
                "modality_scale": 1.0,
                "audio_modality_scale": 1.0,
            }
            for var in ("LTX2_STEPS", "LTX2_GUIDANCE", "LTX2_STG"):
                if os.getenv(var):
                    logger.warning("%s is ignored for the distilled variant (fixed recipe)", var)
    except Exception as exc:
        logger.error(
            "Failed to load %s: %s\nIf this is a 401/403 the weights are "
            "gated — accept the licence at https://huggingface.co/%s and "
            "export HF_TOKEN.", MODEL_ID, exc, MODEL_ID,
        )
        raise

    # ── Placement ──────────────────────────────────────────────────
    offload = os.getenv("LTX2_OFFLOAD", "").lower()
    if offload == "sequential":
        pipe.enable_sequential_cpu_offload(device="cuda:0")
    elif offload == "cpu":
        pipe.enable_model_cpu_offload(device="cuda:0")
    else:
        pipe.to("cuda")

    if os.getenv("LTX2_VAE_TILING") == "1":
        pipe.vae.enable_tiling()
        logger.info("VAE tiling enabled")

    if getattr(pipe, "vocoder", None) is not None:
        _audio_sample_rate = getattr(
            pipe.vocoder.config, "output_sampling_rate", _audio_sample_rate,
        )

    vram = torch.cuda.memory_allocated() / 1e9 if torch.cuda.is_available() else 0
    logger.info(
        "Ready in %.1fs — %s, %d steps, recipe=%s, %dx%d @%.0ffps, audio@%dHz, %.1f GB VRAM",
        time.monotonic() - t0, VARIANT, _steps,
        {k: v for k, v in _call_defaults.items() if k != "sigmas"},
        WIDTH, HEIGHT, FRAME_RATE, _audio_sample_rate, vram,
    )


# ── Endpoints ─────────────────────────────────────────────────────

@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate one chunk. Returns an MP4 with LTX-2.5's native audio."""
    if pipe is None:
        raise HTTPException(503, "Pipeline still loading — try again shortly")
    async with _lock:
        return await asyncio.to_thread(_generate_sync, req)


@app.post("/reset")
async def reset():
    """Drop session state (reference + window). Keeps the pipeline loaded."""
    session.clear()
    logger.info("Session reset")
    return {"status": "reset"}


@app.get("/health")
async def health():
    return {
        "status": "ready" if pipe is not None else "loading",
        "model": MODEL_ID,
        "variant": VARIANT,
        "distilled": VARIANT == "distilled",
        "steps": _steps,
        "recipe": {k: v for k, v in _call_defaults.items() if k != "sigmas"},
        "frame_rate": FRAME_RATE,
        "width": WIDTH,
        "height": HEIGHT,
        "anchor": True,
        "turn_count": session.get("turn_count", 0),
        "has_reference": "ref_image" in session,
        "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 1)
        if torch.cuda.is_available()
        else None,
    }


# ── Generation ────────────────────────────────────────────────────

def _generate_sync(req: GenerateRequest) -> FileResponse:
    from diffusers.pipelines.ltx2.pipeline_ltx2_condition import (
        LTX2VideoCondition,
    )

    if "ref_image" not in session:
        if req.reference_image is None:
            raise HTTPException(
                400,
                "reference_image required on first call (base64 PNG). "
                "Send it again if the server was restarted.",
            )
        session["ref_image"] = _decode_image(req.reference_image)
        session["window"] = None
        session["turn_count"] = 0
        logger.info("Session seeded with reference image")
    elif req.reference_image is not None:
        session["ref_image"] = _decode_image(req.reference_image)
        logger.info("Reference image replaced mid-session")

    session["turn_count"] += 1
    turn = session["turn_count"]

    num_frames = _snap_to_frame_grid(req.num_frames)

    # Exactly one condition at index 0: the reference (turn 1 / cut) or the
    # previous chunk's tail. Two index-0 conditions would overwrite each
    # other's leading tokens.
    if session["window"] is None or req.reset_window:
        conditions = [LTX2VideoCondition(
            frames=session["ref_image"], index=0, strength=1.0,
        )]
        cond_desc = "reference image (cut)" if req.reset_window else "reference image"
    else:
        conditions = [LTX2VideoCondition(
            frames=session["window"],  # uint8 (F, H, W, 3)
            index=0,
            strength=req.continuity_strength,
        )]
        cond_desc = f"{session['window'].shape[0]} trailing frames"

    # Identity anchor: a guiding keyframe (appended tokens, not an
    # overwrite), so it composes with either index-0 condition.
    if req.anchor_strength > 0.0:
        conditions.append(LTX2VideoCondition(
            frames=session["ref_image"], index=req.anchor_index, strength=req.anchor_strength,
        ))
        cond_desc += f" + reference anchor @{req.anchor_index} ×{req.anchor_strength:.2f}"

    seed = req.seed if req.seed is not None else int.from_bytes(
        os.urandom(4), "big",
    )
    generator = torch.Generator(device="cuda").manual_seed(seed)

    logger.info(
        "Turn %d: %d frames on %s (strength=%.2f, seed=%d, %d steps) …",
        turn, num_frames, cond_desc, req.continuity_strength, seed, _steps,
    )
    t0 = time.monotonic()

    call_kwargs: dict = dict(
        conditions=conditions,
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or _negative_default,
        height=HEIGHT,
        width=WIDTH,
        num_frames=num_frames,
        frame_rate=FRAME_RATE,
        generator=generator,
        output_type="np",
        return_dict=False,
        **_call_defaults,
    )

    video, audio = pipe(**call_kwargs)
    dt = time.monotonic() - t0

    # output_type="np" → float [0, 1], shape (B, F, H, W, C).
    frames = video[0] if getattr(video, "ndim", 0) == 5 else video

    # Keep the tail as uint8 [0, 255] for the next turn's condition.
    # Conditions are mapped with `pixels / 127.5 - 1.0`, so the range
    # matters; uint8 also satisfies the single-frame CRF path, which
    # rejects non-uint8 input.
    tail = min(req.continuity_frames, frames.shape[0])
    session["window"] = (
        np.clip(frames[-tail:], 0.0, 1.0) * 255.0
    ).round().astype(np.uint8)

    logger.info("Turn %d: generated in %.2fs, %s", turn, dt, list(frames.shape))

    # ── Encode ────────────────────────────────────────────────────
    # encode_video takes float [0, 1] for ndarray input — hand it the
    # pipeline's output unchanged rather than pre-converting.
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()

    audio_arg = None
    if audio is not None:
        a = audio[0] if getattr(audio, "ndim", 0) > 2 else audio
        audio_arg = (
            torch.from_numpy(a).float() if isinstance(a, np.ndarray)
            else a.float().cpu()
        )

    _encode_video(
        frames,
        fps=int(FRAME_RATE),
        output_path=tmp.name,
        audio=audio_arg,
        audio_sample_rate=_audio_sample_rate if audio_arg is not None else None,
    )

    duration_s = frames.shape[0] / FRAME_RATE
    logger.info(
        "Turn %d: %.2fs clip, %.1f GB VRAM → %s",
        turn, duration_s, torch.cuda.memory_allocated() / 1e9, tmp.name,
    )

    return FileResponse(
        tmp.name,
        media_type="video/mp4",
        filename=f"turn_{turn:04d}.mp4",
        headers={
            "X-Turn": str(turn),
            "X-Latency-S": f"{dt:.2f}",
            "X-Num-Frames": str(frames.shape[0]),
            "X-Duration-S": f"{duration_s:.3f}",
            "X-Frame-Rate": str(FRAME_RATE),
            "X-Has-Audio": "1" if audio_arg is not None else "0",
            "X-Variant": VARIANT,
        },
        background=BackgroundTask(_cleanup, tmp.name),
    )


# ── Helpers ───────────────────────────────────────────────────────

def _snap_to_frame_grid(raw: int) -> int:
    """Snap onto the VAE's causal (N*8 + 1) temporal grid."""
    return ((max(raw, 1) - 2) // 8 + 1) * 8 + 1


def _decode_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


if __name__ == "__main__":
    host = os.getenv("LTX2_HOST", "0.0.0.0")
    port = int(os.getenv("LTX2_PORT", "8000"))
    logger.info("Starting LTX-2.5 server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
