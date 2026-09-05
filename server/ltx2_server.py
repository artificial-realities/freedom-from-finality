"""Resident LTX-2.5 inference server.

Holds the LTX-2.5 pipeline in GPU memory and serves /generate requests.
Manages sliding-window state internally — the client just sends prompts.
Uses the diffusers LTX2ConditionPipeline for multi-frame conditioning
(continuity from last frame + identity anchor from reference image).

Native audio: LTX-2.5 generates synchronized audio alongside video — the
returned MP4 includes audio automatically.

Setup on a GPU box (≥80GB VRAM recommended for bf16; works on 24GB with fp8):

    pip install diffusers[torch] transformers accelerate sentencepiece
    pip install fastapi uvicorn

    python ltx2_server.py
    # → Downloads model on first run (~66GB), then serves on :8000

Environment variables:
    LTX2_MODEL        HuggingFace model id (default: Lightricks/LTX-2.5)
    LTX2_HOST         bind host (default: 0.0.0.0)
    LTX2_PORT         bind port (default: 8000)
    LTX2_QUANTIZE     set to "fp8" for fp8 quantization (fits 24GB cards)
    LTX2_OFFLOAD      set to "cpu" or "sequential" for memory-constrained GPUs
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
from pydantic import BaseModel
from starlette.background import BackgroundTask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ltx2_server")

# ── Defaults ───────────────────────────────────────────────────────
MODEL_ID = os.getenv("LTX2_MODEL", "Lightricks/LTX-2.5")
WIDTH = 768
HEIGHT = 512
FRAME_RATE = 24.0
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted"

# ── Server state ──────────────────────────────────────────────────
app = FastAPI(title="LTX-2.5 Inference Server")
pipe = None  # set at startup
_encode_video = None  # diffusers export helper
_audio_sample_rate: int = 24000
session: dict = {}  # ref_image, last_frame, turn_count
_lock = asyncio.Lock()


# ── Request model ─────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    reference_image: str | None = None  # base64 PNG — required on first call
    anchor_strength: float = 0.5
    seed: int | None = None
    num_frames: int = 121  # ~5s at 24fps; must be 8k+1


# ── Pipeline loading ──────────────────────────────────────────────

@app.on_event("startup")
async def load_pipeline() -> None:
    global pipe, _encode_video, _audio_sample_rate

    logger.info("Loading LTX-2.5 from %s …", MODEL_ID)
    t0 = time.monotonic()

    # Import here so startup logs show before the heavy import.
    from diffusers.pipelines.ltx2.export_utils import encode_video as _ev

    _encode_video = _ev

    # Try the multi-condition pipeline first; fall back to i2v.
    try:
        from diffusers import LTX2ConditionPipeline
        logger.info("Using LTX2ConditionPipeline (multi-condition support)")
        pipe = LTX2ConditionPipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
        )
    except (ImportError, Exception) as exc:
        logger.warning(
            "LTX2ConditionPipeline not available (%s), "
            "falling back to LTX2ImageToVideoPipeline",
            exc,
        )
        from diffusers import LTX2ImageToVideoPipeline
        pipe = LTX2ImageToVideoPipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
        )

    # Memory strategy.
    offload = os.getenv("LTX2_OFFLOAD", "").lower()
    quantize = os.getenv("LTX2_QUANTIZE", "").lower()

    if quantize == "fp8":
        logger.info("Applying fp8 quantization to transformer")
        pipe.transformer = pipe.transformer.to(torch.float8_e4m3fn)

    if offload == "sequential":
        pipe.enable_sequential_cpu_offload(device="cuda:0")
    elif offload == "cpu":
        pipe.enable_model_cpu_offload(device="cuda:0")
    else:
        pipe.to("cuda")

    # Cache the vocoder sample rate for audio encoding.
    if hasattr(pipe, "vocoder") and pipe.vocoder is not None:
        _audio_sample_rate = getattr(
            pipe.vocoder.config, "output_sampling_rate", 24000,
        )

    dt = time.monotonic() - t0
    vram = torch.cuda.memory_allocated() / 1e9 if torch.cuda.is_available() else 0
    logger.info("Pipeline ready in %.1fs (%.1f GB VRAM)", dt, vram)


# ── Endpoints ─────────────────────────────────────────────────────

@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate one video chunk. Returns MP4 (with native audio)."""
    if pipe is None:
        raise HTTPException(503, "Pipeline still loading — try again shortly")
    async with _lock:
        result = await asyncio.to_thread(_generate_sync, req)
    return result


@app.post("/reset")
async def reset():
    """Clear session state. Keeps the pipeline loaded."""
    session.clear()
    logger.info("Session reset")
    return {"status": "reset"}


@app.get("/health")
async def health():
    return {
        "status": "ready" if pipe is not None else "loading",
        "model": MODEL_ID,
        "turn_count": session.get("turn_count", 0),
        "has_reference": "ref_image" in session,
        "frame_rate": FRAME_RATE,
        "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 1)
        if torch.cuda.is_available()
        else None,
    }


# ── Sync generation (runs in a thread) ───────────────────────────

def _generate_sync(req: GenerateRequest) -> FileResponse:
    from diffusers.pipelines.ltx2.pipeline_ltx2_condition import (
        LTX2VideoCondition,
    )

    # Session init — first call must carry the reference image.
    if "ref_image" not in session:
        if req.reference_image is None:
            raise HTTPException(
                400,
                "reference_image required on first call (base64 PNG). "
                "Send it again if the server was restarted.",
            )
        session["ref_image"] = _decode_image(req.reference_image)
        session["last_frame"] = None
        session["turn_count"] = 0
        logger.info("Session initialised with reference image")
    elif req.reference_image is not None:
        session["ref_image"] = _decode_image(req.reference_image)
        logger.info("Reference image updated mid-session")

    session["turn_count"] += 1
    turn = session["turn_count"]

    # Pad num_frames to (N*8 + 1) as required by the pipeline.
    num_frames = ((req.num_frames - 2) // 8 + 1) * 8 + 1

    # ── Build conditioning ──────────────────────────────────────
    conditions: list[LTX2VideoCondition] = []

    if session["last_frame"] is None:
        # First turn — condition on reference image.
        conditions.append(
            LTX2VideoCondition(
                frames=session["ref_image"],
                index=0,
                strength=1.0,
            )
        )
    else:
        # Subsequent — continuity from last frame + reference anchor.
        conditions.append(
            LTX2VideoCondition(
                frames=session["last_frame"],
                index=0,
                strength=1.0,
            )
        )
        if req.anchor_strength > 0:
            conditions.append(
                LTX2VideoCondition(
                    frames=session["ref_image"],
                    index=0,
                    strength=req.anchor_strength,
                )
            )

    seed = req.seed if req.seed is not None else int.from_bytes(
        os.urandom(4), "big",
    )
    generator = torch.Generator(device="cuda").manual_seed(seed)

    logger.info(
        "Turn %d: generating %d frames (seed=%d, anchor=%.2f) …",
        turn, num_frames, seed, req.anchor_strength,
    )
    t0 = time.monotonic()

    # ── Run the pipeline ────────────────────────────────────────
    call_kwargs: dict = dict(
        conditions=conditions,
        prompt=req.prompt,
        negative_prompt=NEGATIVE,
        height=HEIGHT,
        width=WIDTH,
        num_frames=num_frames,
        frame_rate=FRAME_RATE,
        generator=generator,
        guidance_scale=3.0,
        output_type="np",
        return_dict=False,
    )

    result = pipe(**call_kwargs)

    # The pipeline returns (video, audio) — both as numpy arrays.
    if isinstance(result, tuple) and len(result) >= 2:
        video_np, audio_np = result[0], result[1]
    else:
        # Fallback if the pipeline returns a different structure.
        video_np = result[0] if isinstance(result, tuple) else result.frames
        audio_np = None

    dt = time.monotonic() - t0

    # video_np shape: (B, F, H, W, 3) or (F, H, W, 3) — normalise.
    if video_np.ndim == 5:
        video_np = video_np[0]

    # Trim to requested frame count.
    video_np = video_np[:req.num_frames]

    logger.info(
        "Turn %d: generated in %.2fs, shape %s",
        turn, dt, list(video_np.shape),
    )

    # Store the last frame for sliding-window conditioning.
    last_frame = video_np[-1]  # (H, W, 3), float 0-1
    if last_frame.max() <= 1.0:
        last_frame = (last_frame * 255).astype(np.uint8)
    session["last_frame"] = Image.fromarray(last_frame)

    # ── Encode to MP4 ──────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()

    # Ensure frames are uint8 0-255 for encoding.
    if video_np.dtype != np.uint8:
        if video_np.max() <= 1.0:
            video_np = (video_np * 255).astype(np.uint8)
        else:
            video_np = video_np.astype(np.uint8)

    if audio_np is not None and _encode_video is not None:
        # Use diffusers' encode_video which muxes audio natively.
        audio_tensor = (
            torch.from_numpy(audio_np[0]).float()
            if isinstance(audio_np, np.ndarray) and audio_np.ndim > 1
            else torch.from_numpy(audio_np).float()
            if isinstance(audio_np, np.ndarray)
            else audio_np[0].float().cpu()
        )
        _encode_video(
            video_np,
            fps=FRAME_RATE,
            audio=audio_tensor,
            audio_sample_rate=_audio_sample_rate,
            output_path=tmp.name,
        )
    else:
        # No audio — fall back to imageio.
        _save_video_imageio(video_np, tmp.name, fps=int(FRAME_RATE))

    duration_s = video_np.shape[0] / FRAME_RATE
    vram = torch.cuda.memory_allocated() / 1e9

    logger.info(
        "Turn %d: %.2fs clip, %.1f GB VRAM → %s",
        turn, duration_s, vram, tmp.name,
    )

    return FileResponse(
        tmp.name,
        media_type="video/mp4",
        filename=f"turn_{turn:04d}.mp4",
        headers={
            "X-Turn": str(turn),
            "X-Latency-S": f"{dt:.2f}",
            "X-Num-Frames": str(video_np.shape[0]),
            "X-Duration-S": f"{duration_s:.3f}",
            "X-Frame-Rate": str(FRAME_RATE),
        },
        background=BackgroundTask(_cleanup, tmp.name),
    )


# ── Helpers ───────────────────────────────────────────────────────

def _decode_image(b64: str) -> Image.Image:
    """Decode a base64 PNG into a PIL Image."""
    img_bytes = base64.b64decode(b64)
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


def _save_video_imageio(
    frames_np: np.ndarray, path: str, fps: int = 24,
) -> None:
    """Save (F, H, W, 3) uint8 numpy array as an MP4 (no audio)."""
    import imageio

    with imageio.get_writer(path, fps=fps) as writer:
        for frame in frames_np:
            writer.append_data(frame)


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("LTX2_HOST", "0.0.0.0")
    port = int(os.getenv("LTX2_PORT", "8000"))
    logger.info("Starting LTX-2.5 server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
