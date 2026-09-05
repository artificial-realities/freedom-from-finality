"""Resident LTXV inference server.

Holds the LTXV pipeline in GPU memory and serves /generate requests.
Manages sliding-window state internally — the client just sends prompts.

Setup on a GPU box (≥80GB VRAM for 13b):

    git clone https://github.com/Lightricks/LTX-Video.git
    cd LTX-Video && pip install -e .
    pip install fastapi uvicorn av imageio[ffmpeg]

    # Start the server (from inside the LTX-Video repo):
    LTXV_CONFIG=configs/ltxv-13b-0.9.8-distilled.yaml python ltxv_server.py

    # Or point LTXV_REPO_PATH at the repo and run from anywhere:
    LTXV_REPO_PATH=/root/LTX-Video python server/ltxv_server.py

Environment variables:
    LTXV_CONFIG      config yaml relative to the LTX-Video repo
                     (default: configs/ltxv-13b-0.9.8-distilled.yaml)
    LTXV_REPO_PATH   path to cloned LTX-Video repo (default: current dir)
    LTXV_HOST         bind host (default: 0.0.0.0)
    LTXV_PORT         bind port (default: 8000)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel
from starlette.background import BackgroundTask

# ── LTX-Video imports (repo must be on sys.path) ────────────────────
REPO_PATH = os.getenv("LTXV_REPO_PATH", ".")
if REPO_PATH not in sys.path:
    sys.path.insert(0, REPO_PATH)

from huggingface_hub import hf_hub_download  # noqa: E402
from ltx_video.inference import (  # noqa: E402
    create_ltx_video_pipeline,
    load_pipeline_config,
    load_media_file,
    calculate_padding,
)
from ltx_video.pipelines.pipeline_ltx_video import (  # noqa: E402
    ConditioningItem,
    LTXVideoPipeline,
)
from ltx_video.utils.skip_layer_strategy import SkipLayerStrategy  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ltxv_server")

# ── Generation defaults ─────────────────────────────────────────────
CONFIG_FILE = os.getenv("LTXV_CONFIG", "configs/ltxv-13b-0.9.8-distilled.yaml")
WIDTH = 768
HEIGHT = 512
CHUNK_FRAMES = 41
COND_TAIL_FRAMES = 9
FRAME_RATE = 30
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted"

# ── Server state ────────────────────────────────────────────────────
app = FastAPI(title="LTXV Inference Server")
pipe: LTXVideoPipeline | None = None
pipe_config: dict = {}
session: dict = {}  # ref_tensor, last_output, turn_count
_lock = asyncio.Lock()

# Pre-computed padding for the configured resolution.
_padding: tuple[int, int, int, int] = (0, 0, 0, 0)
_height_padded: int = HEIGHT
_width_padded: int = WIDTH


# ── Request model ──────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    reference_image: str | None = None  # base64 PNG — required on first call
    anchor_strength: float = 0.5
    seed: int | None = None
    num_frames: int = CHUNK_FRAMES
    cond_tail_frames: int = COND_TAIL_FRAMES


# ── Pipeline loading ────────────────────────────────────────────────

@app.on_event("startup")
async def load_pipeline() -> None:
    global pipe, pipe_config, _padding, _height_padded, _width_padded

    logger.info("Loading pipeline config from %s …", CONFIG_FILE)
    pipe_config = load_pipeline_config(CONFIG_FILE)

    # Resolve checkpoint — download from HF hub if not local.
    ckpt_name = pipe_config["checkpoint_path"]
    if not os.path.isfile(ckpt_name):
        logger.info("Downloading checkpoint %s from HF hub …", ckpt_name)
        ckpt_path = hf_hub_download(
            repo_id="Lightricks/LTX-Video",
            filename=ckpt_name,
            repo_type="model",
        )
    else:
        ckpt_path = ckpt_name

    precision = pipe_config["precision"]
    text_encoder = pipe_config["text_encoder_model_name_or_path"]
    sampler = pipe_config.get("sampler")

    logger.info("Creating pipeline (precision=%s) …", precision)
    t0 = time.monotonic()

    pipe = create_ltx_video_pipeline(
        ckpt_path=ckpt_path,
        precision=precision,
        text_encoder_model_name_or_path=text_encoder,
        sampler=sampler,
        device="cuda",
        enhance_prompt=False,  # Spielberg writes the prompt — no enhancement.
    )

    # Pre-compute padding for our fixed resolution.
    _height_padded = ((HEIGHT - 1) // 32 + 1) * 32
    _width_padded = ((WIDTH - 1) // 32 + 1) * 32
    _padding = calculate_padding(HEIGHT, WIDTH, _height_padded, _width_padded)

    dt = time.monotonic() - t0
    vram = torch.cuda.memory_allocated() / 1e9
    logger.info(
        "Pipeline ready in %.1fs (%.1f GB VRAM, %dx%d padded to %dx%d)",
        dt, vram, WIDTH, HEIGHT, _width_padded, _height_padded,
    )


# ── Endpoints ───────────────────────────────────────────────────────

@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate one video chunk. Returns the MP4 as a binary download."""
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
        "turn_count": session.get("turn_count", 0),
        "has_reference": "ref_tensor" in session,
        "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 1)
        if torch.cuda.is_available()
        else None,
    }


# ── Sync generation (runs in a thread) ─────────────────────────────

def _generate_sync(req: GenerateRequest) -> FileResponse:
    # Session init — first call must carry the reference image.
    if "ref_tensor" not in session:
        if req.reference_image is None:
            raise HTTPException(
                400,
                "reference_image required on first call (base64 PNG). "
                "Send it again if the server was restarted.",
            )
        session["ref_tensor"] = _decode_image_to_tensor(req.reference_image)
        session["last_output"] = None
        session["turn_count"] = 0
        logger.info("Session initialised with reference image")
    elif req.reference_image is not None:
        session["ref_tensor"] = _decode_image_to_tensor(req.reference_image)
        logger.info("Reference image updated mid-session")

    session["turn_count"] += 1
    turn = session["turn_count"]
    ref_tensor = session["ref_tensor"]

    # Pad num_frames to (N*8 + 1) as required by the pipeline.
    num_frames_padded = ((req.num_frames - 2) // 8 + 1) * 8 + 1

    # ── Conditioning items ──────────────────────────────────────────
    conditioning_items: list[ConditioningItem] = []

    if session["last_output"] is None:
        # First turn — condition on the reference image.
        conditioning_items.append(
            ConditioningItem(
                media_item=ref_tensor,
                media_frame_number=0,
                conditioning_strength=1.0,
            )
        )
    else:
        # Subsequent — sliding window tail + reference anchor.
        last_out = session["last_output"]  # (1, 3, F, H, W)
        tail_frames = last_out[:, :, -req.cond_tail_frames :, :, :]
        conditioning_items.append(
            ConditioningItem(
                media_item=tail_frames,
                media_frame_number=0,
                conditioning_strength=1.0,
            )
        )
        if req.anchor_strength > 0:
            conditioning_items.append(
                ConditioningItem(
                    media_item=ref_tensor,
                    media_frame_number=0,
                    conditioning_strength=req.anchor_strength,
                )
            )

    seed = req.seed if req.seed is not None else int.from_bytes(os.urandom(4), "big")
    generator = torch.Generator(device="cuda").manual_seed(seed)

    # Read generation params from the pipeline config.
    # For multi-scale configs we use first_pass params in single-pass mode.
    pass_cfg = pipe_config.get("first_pass", pipe_config)
    timesteps = pass_cfg.get("timesteps")
    guidance_scale = pass_cfg.get("guidance_scale", 1.0)
    stg_scale = pass_cfg.get("stg_scale", 0)
    rescaling_scale = pass_cfg.get("rescaling_scale", 1.0)
    skip_block_list = pass_cfg.get("skip_block_list", [])
    num_inference_steps = pipe_config.get("num_inference_steps", 8)
    decode_timestep = pipe_config.get("decode_timestep", 0.05)
    decode_noise_scale = pipe_config.get("decode_noise_scale", 0.025)
    stochastic_sampling = pipe_config.get("stochastic_sampling", False)

    # Resolve skip-layer strategy from config.
    stg_mode = pipe_config.get("stg_mode", "attention_values").lower()
    strategy_map = {
        "attention_values": SkipLayerStrategy.AttentionValues,
        "stg_av": SkipLayerStrategy.AttentionValues,
        "attention_skip": SkipLayerStrategy.AttentionSkip,
        "stg_as": SkipLayerStrategy.AttentionSkip,
        "residual": SkipLayerStrategy.Residual,
        "stg_r": SkipLayerStrategy.Residual,
        "transformer_block": SkipLayerStrategy.TransformerBlock,
        "stg_t": SkipLayerStrategy.TransformerBlock,
    }
    skip_layer_strategy = strategy_map.get(stg_mode, SkipLayerStrategy.AttentionValues)

    logger.info(
        "Turn %d: generating %d frames (seed=%d, anchor=%.2f, steps=%s) …",
        turn, req.num_frames, seed, req.anchor_strength,
        timesteps if timesteps else num_inference_steps,
    )
    t0 = time.monotonic()

    call_kwargs = dict(
        prompt=req.prompt,
        negative_prompt=NEGATIVE,
        prompt_attention_mask=None,
        negative_prompt_attention_mask=None,
        height=_height_padded,
        width=_width_padded,
        num_frames=num_frames_padded,
        frame_rate=FRAME_RATE,
        generator=generator,
        output_type="pt",
        conditioning_items=conditioning_items,
        guidance_scale=guidance_scale,
        stg_scale=stg_scale,
        rescaling_scale=rescaling_scale,
        skip_layer_strategy=skip_layer_strategy,
        skip_block_list=skip_block_list,
        decode_timestep=decode_timestep,
        decode_noise_scale=decode_noise_scale,
        stochastic_sampling=stochastic_sampling,
        is_video=True,
        vae_per_channel_normalize=True,
        mixed_precision=(pipe_config.get("precision") == "mixed_precision"),
        enhance_prompt=False,
    )
    if timesteps:
        call_kwargs["timesteps"] = timesteps
    else:
        call_kwargs["num_inference_steps"] = num_inference_steps

    images = pipe(**call_kwargs).images  # (B, C, F, H, W)

    dt = time.monotonic() - t0

    # Crop padding and trim to requested frame count.
    pad_left, pad_right, pad_top, pad_bottom = _padding
    crop_bottom = -pad_bottom if pad_bottom else images.shape[3]
    crop_right = -pad_right if pad_right else images.shape[4]
    images = images[:, :, :req.num_frames, pad_top:crop_bottom, pad_left:crop_right]

    logger.info("Turn %d: generated in %.2fs, shape %s", turn, dt, list(images.shape))

    # Store the full output for sliding-window conditioning next turn.
    session["last_output"] = images.detach()

    # Convert to numpy frames and save as MP4.
    video_np = images[0].permute(1, 2, 3, 0).cpu().float().numpy()
    video_np = (video_np * 255).astype(np.uint8)

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    _save_video(video_np, tmp.name, fps=FRAME_RATE)

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
        },
        background=BackgroundTask(_cleanup, tmp.name),
    )


# ── Helpers ─────────────────────────────────────────────────────────

def _decode_image_to_tensor(b64: str) -> torch.Tensor:
    """Decode a base64 PNG into the (1, 3, 1, H, W) tensor the pipeline expects."""
    img_bytes = base64.b64decode(b64)
    # Save to a temp file so we can reuse load_media_file's resize/crop logic.
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    try:
        tensor = load_media_file(
            media_path=tmp.name,
            height=HEIGHT,
            width=WIDTH,
            max_frames=1,
            padding=_padding,
            just_crop=True,
        )
    finally:
        os.unlink(tmp.name)
    return tensor


def _save_video(frames_np: np.ndarray, path: str, fps: int = 30) -> None:
    """Save (F, H, W, 3) uint8 numpy array as an MP4."""
    import imageio  # noqa: WPS433

    with imageio.get_writer(path, fps=fps) as writer:
        for frame in frames_np:
            writer.append_data(frame)


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("LTXV_HOST", "0.0.0.0")
    port = int(os.getenv("LTXV_PORT", "8000"))
    logger.info("Starting LTXV server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
