"""Resident LTX-2.5 inference server.

Holds the LTX-2.5 pipeline in GPU memory and serves /generate requests.
The client sends only a prompt; this server owns the sliding window that
carries the story from one chunk to the next.

Continuity model
----------------
LTX-2.5's `LTX2ConditionPipeline` applies a first-frame condition by
*overwriting* latent tokens:

    latents[:, start:end] = cond
    conditioning_mask[:, start:end] = strength

Conditions are applied in list order, so two conditions at `index=0`
overlap and the later one wins over the earlier one's leading tokens.
There is therefore no way to blend a second "identity anchor" image on
top of a continuity window the way LTX-Video 0.9's `ConditioningItem`
allowed — a low-strength anchor at index 0 would just clobber the front
of the window. So we send exactly one first-frame condition:

  * turn 1  — the reference image, strength 1.0, which seeds the chain
  * turn N  — the trailing `continuity_frames` of the previous chunk,
              at `continuity_strength`

`continuity_strength` is the steering knob: 1.0 holds the previous chunk
hard (and largely ignores a new prompt), lower values let the prompt
through at the cost of visual continuity.

Frames are kept in memory as uint8 [0, 255] and handed straight back as
the next condition — no PNG round trip, no decode/re-encode loss. The
pipeline maps conditions with `pixels / 127.5 - 1.0`, so the [0, 255]
range matters; float [0, 1] would condition on near-black.

Native audio: LTX-2.5 generates a synchronized audio track, so the MP4
this server returns already carries ambient sound and effects.

Setup on a GPU box:

    pip install "diffusers[torch]" transformers accelerate sentencepiece av
    pip install fastapi uvicorn

    # The weights are GATED. One-time, on huggingface.co:
    #   1. Log in, open https://huggingface.co/Lightricks/LTX-2.5-Diffusers
    #   2. Click "Agree and access repository" (auto-approved, instant)
    #   3. Make a token at https://huggingface.co/settings/tokens
    export HF_TOKEN=hf_...

    python ltx2_server.py

Environment variables:
    HF_TOKEN          HuggingFace token (required — the weights are gated)
    LTX2_MODEL        model id (default: Lightricks/LTX-2.5-Diffusers)
    LTX2_DISTILLED    "1" to enable the distilled LoRA (default: off — broken
                      with diffusers ≤0.41 because connector modules are not
                      wired into the pipeline; re-enable when supported)
    LTX2_STEPS        override sampling steps (default: 8 distilled / 15 base)
    LTX2_STG          spatio-temporal guidance (default: 0.0 distilled, 1.0
                      base). Non-zero costs an extra forward pass per step.
    LTX2_WIDTH        output width, multiple of 32 (default: 768)
    LTX2_HEIGHT       output height, multiple of 32 (default: 512)
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
DISTILLED_LORA = "ltx-2.5-22b-distilled-lora-450-bf16.safetensors"
# The distilled LoRA is OFF by default: diffusers ≤0.41 does not wire up
# the `audio_embeddings_connector` and `video_embeddings_connector`
# modules, so the LoRA loads only partially and the 8-step schedule
# produces corrupted output (temporal noise within 1-2 seconds).
# Re-enable with LTX2_DISTILLED=1 once diffusers supports those.
USE_DISTILLED = os.getenv("LTX2_DISTILLED", "0") != "0"

WIDTH = int(os.getenv("LTX2_WIDTH", "768"))
HEIGHT = int(os.getenv("LTX2_HEIGHT", "512"))
FRAME_RATE = 24.0
NEGATIVE = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, static"
)

# ── Server state ──────────────────────────────────────────────────
app = FastAPI(title="LTX-2.5 Inference Server")
pipe = None
_encode_video = None
_sigmas: list[float] | None = None
_steps: int = 30
_guidance: float = 3.0
_stg: float = 1.0
_audio_sample_rate: int = 24000

# Session: the reference image (PIL, seeds turn 1) and the tail of the
# last generated chunk (uint8 ndarray, shape (F, H, W, 3)).
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


# ── Startup ───────────────────────────────────────────────────────

@app.on_event("startup")
async def load_pipeline() -> None:
    global pipe, _encode_video, _sigmas, _steps, _guidance, _stg
    global _audio_sample_rate

    from diffusers import LTX2ConditionPipeline
    from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES
    from diffusers.utils import encode_video

    _encode_video = encode_video

    if not (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")):
        logger.warning(
            "No HF_TOKEN set — %s is gated and the download will fail. "
            "Accept the licence on the model page, then export HF_TOKEN.",
            MODEL_ID,
        )

    logger.info("Loading %s …", MODEL_ID)
    t0 = time.monotonic()
    try:
        pipe = LTX2ConditionPipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
        )
    except Exception as exc:
        logger.error(
            "Failed to load %s: %s\nIf this is a 401/403 the weights are "
            "gated — accept the licence at https://huggingface.co/%s and "
            "export HF_TOKEN.", MODEL_ID, exc, MODEL_ID,
        )
        raise

    # ── Distilled schedule: 8 steps, CFG off ───────────────────────
    if USE_DISTILLED:
        logger.info("Applying distilled LoRA %s", DISTILLED_LORA)
        pipe.load_lora_weights(
            MODEL_ID, weight_name=DISTILLED_LORA, adapter_name="distilled",
        )
        pipe.set_adapters("distilled", 1.0)
        # Fuse the LoRA into the base weights and drop the adapter
        # overhead — this brings VRAM back down to base-model size so
        # the full pipeline fits in 80 GB without CPU offload.
        pipe.fuse_lora()
        pipe.unload_lora_weights()
        logger.info("LoRA fused into base weights and adapter unloaded")
        _sigmas = list(DISTILLED_SIGMA_VALUES)
        _steps = len(_sigmas)
        # guidance 1.0 disables CFG, which also skips the negative
        # forward pass — roughly halves the work per step.
        _guidance = 1.0
        _stg = 0.0
    else:
        # 15 steps is the sweet spot: indistinguishable from 30 at half the
        # cost. Set LTX2_STEPS=30 to A/B or trade latency for quality.
        _sigmas, _steps, _guidance, _stg = None, 15, 3.0, 1.0

    if os.getenv("LTX2_STEPS"):
        _steps = int(os.environ["LTX2_STEPS"])
        _sigmas = None  # an explicit step count overrides the fixed schedule
    if os.getenv("LTX2_STG"):
        _stg = float(os.environ["LTX2_STG"])

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
            pipe.vocoder.config, "output_sampling_rate", 24000,
        )

    vram = torch.cuda.memory_allocated() / 1e9 if torch.cuda.is_available() else 0
    logger.info(
        "Ready in %.1fs — %s, %d steps, guidance=%.1f, stg=%.1f, "
        "%dx%d @%.0ffps, audio@%dHz, %.1f GB VRAM",
        time.monotonic() - t0, "distilled" if USE_DISTILLED else "base",
        _steps, _guidance, _stg, WIDTH, HEIGHT, FRAME_RATE,
        _audio_sample_rate, vram,
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
        "distilled": USE_DISTILLED,
        "steps": _steps,
        "frame_rate": FRAME_RATE,
        "width": WIDTH,
        "height": HEIGHT,
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

    # Exactly one first-frame condition — see the module docstring for
    # why a second one at index 0 cannot work.
    if session["window"] is None or req.reset_window:
        condition = LTX2VideoCondition(
            frames=session["ref_image"], index=0, strength=1.0,
        )
        cond_desc = "reference image (cut)" if req.reset_window else "reference image"
    else:
        condition = LTX2VideoCondition(
            frames=session["window"],  # uint8 (F, H, W, 3)
            index=0,
            strength=req.continuity_strength,
        )
        cond_desc = f"{session['window'].shape[0]} trailing frames"

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
        conditions=[condition],
        prompt=req.prompt,
        negative_prompt=NEGATIVE,
        height=HEIGHT,
        width=WIDTH,
        num_frames=num_frames,
        frame_rate=FRAME_RATE,
        generator=generator,
        guidance_scale=_guidance,
        stg_scale=_stg,
        output_type="np",
        return_dict=False,
    )
    if _sigmas is not None:
        call_kwargs["sigmas"] = _sigmas
    else:
        call_kwargs["num_inference_steps"] = _steps

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
