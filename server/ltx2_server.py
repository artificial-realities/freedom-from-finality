"""Resident LTX-2.5 inference server.

Holds the LTX-2.5 pipeline in GPU memory and serves /generate requests.
The client sends only a prompt; this server owns the sliding window that
carries the picture from one clip to the next.

The recipe
----------
`Lightricks/LTX-2.5-Diffusers` loads the **distilled** DiT, which runs
unguided on the fixed `DISTILLED_SIGMA_VALUES` schedule (8 steps, one
forward each). The full model's recipe on these weights (15 steps, CFG 3,
STG) made the figure drift within every clip; the distilled recipe holds
it. Everything is resident on an 80 GB card (about 72 GB), the DiT's large
Linear layers run as fp8 matmuls (torchao; Hopper or newer, bf16 on older
cards), its transformer blocks are compiled, and clips render at 512x288,
exactly 16:9. The frames become uint8 and planar YUV on the GPU and are
piped into ffmpeg (libx264), and every clip's audio is padded to the
video and held under -18 LUFS (`audio_ceiling.py`).

Continuity
----------
`LTX2ConditionPipeline` takes `LTX2VideoCondition(frames, index=0,
strength)`: the reference image for the first clip, otherwise the last
`continuity_frames` of the previous clip at `continuity_strength` (1.0
holds the previous clip hard and largely ignores a new prompt; lower lets
the prompt through at the cost of continuity). A client run's first
request (`start`) begins a new session from either the reference image (a
new story) or the client's copy of the frames to continue from
(`window_frames`: a story resuming, or this server restarted), and
nothing from an earlier session carries over: neither its picture nor its
clip ids.

Warm-up
-------
Under fp8 every frame count compiles for 90-170 s, so the server renders
throwaway clips at startup at the counts in LTX2_WARMUP_FRAMES (default
the video config's one shape, 153 frames, three times). /health says
"warming" until then and lists `warm_frames`; the client refuses to start
when its shape is not among them.

Environment:
    HF_TOKEN             Hugging Face token (required: the weights are gated)
    LTX2_WARMUP_FRAMES   frame counts to warm (`python -m src.i2v.shape <config>` prints them)
    LTX2_HOST/PORT       bind address (default 127.0.0.1:8000: reach it through an ssh
                         tunnel or from the same machine; it has no authentication)
"""

from __future__ import annotations

import os

# Before torch and huggingface_hub are imported: fast weight downloads, and
# an allocator that does not fragment around the VAE decode.
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import asyncio  # noqa: E402
import base64  # noqa: E402
import io  # noqa: E402
import logging  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from PIL import Image  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402

import encode  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ltx2_server")

# "Lightricks/LTX-2.5" is the ComfyUI layout with no model_index.json;
# "-Diffusers" is the same weights in the layout from_pretrained() expects.
MODEL_ID = "Lightricks/LTX-2.5-Diffusers"
WIDTH, HEIGHT = 512, 288
FRAME_RATE = 24.0
AUDIO_CEILING_LUFS = -18.0
X264_THREADS = 8   # ffmpeg's own choice spawns far more threads than a 512x288 clip can use

# ── Server state ──────────────────────────────────────────────────
app = FastAPI(title="LTX-2.5 Inference Server")
pipe = None
_call_defaults: dict = {}   # the distilled recipe: sigmas and guidance off
_audio_sample_rate: int = 48000
_ready: bool = False        # True once the warm-up has run; /generate refuses until then
_warm_frames: set[int] = set()   # frame counts warmed at startup (on the 8k+1 grid), reported by /health
_quant: str = "bf16"
_compiled: bool = False

# Session: the reference image (PIL, seeds the first clip) and the tail of
# the last generated clip (uint8 ndarray, shape (F, H, W, 3)).
session: dict = {}
# This process's identity: a restart gives a new one (/health, X-Session-Epoch),
# so a client can tell a restarted server from a reset session.
SESSION_EPOCH = os.urandom(4).hex()
_lock = asyncio.Lock()


class GenerateRequest(BaseModel):
    prompt: str
    # A client run's first request: a new session, from `reference_image` or
    # from `window_frames`.
    start: bool = False
    reference_image: str | None = None  # base64 PNG: seeds a new session
    continuity_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    continuity_frames: int = Field(default=9, ge=1)
    seed: int | None = None
    num_frames: int = Field(default=153, ge=9)
    # The client's copy of the frames to continue from (base64 PNGs): a
    # story resuming, a server restart, or a parent this server no
    # longer holds. Always adopted, so costume and position carry over.
    window_frames: list[str] | None = None
    # Fenced renders: the clip this render belongs to and the clip it
    # continues from. The server keeps the last few tails keyed by clip_id
    # and conditions on the named parent; an unknown parent returns 409.
    clip_id: int | None = None
    parent_clip_id: int | None = None


# ── Startup ───────────────────────────────────────────────────────

@app.on_event("startup")
async def load_pipeline() -> None:
    global pipe, _call_defaults, _audio_sample_rate, _ready, _quant, _compiled

    from diffusers import LTX2ConditionPipeline
    from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES

    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on the PATH; the server encodes every clip with it")
    if not (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")):
        logger.warning("No HF_TOKEN set: %s is gated and the download will fail. "
                       "Accept the licence on the model page, then export HF_TOKEN.", MODEL_ID)

    logger.info("Loading %s …", MODEL_ID)
    t0 = time.monotonic()
    try:
        pipe = LTX2ConditionPipeline.from_pretrained(MODEL_ID, dtype=torch.bfloat16)
    except Exception as exc:
        logger.error("Failed to load %s: %s\nIf this is a 401/403 the weights are gated: accept the licence "
                     "at https://huggingface.co/%s and export HF_TOKEN.", MODEL_ID, exc, MODEL_ID)
        raise
    # The distilled recipe from the model card: the fixed sigma schedule,
    # unguided, STG and modality guidance off, one forward per step.
    _call_defaults = {
        "sigmas": list(DISTILLED_SIGMA_VALUES),
        "guidance_scale": 1.0,
        "audio_guidance_scale": 1.0,
        "stg_scale": 0.0,
        "audio_stg_scale": 0.0,
        "modality_scale": 1.0,
        "audio_modality_scale": 1.0,
    }
    # Everything resident. The Diffusers pack ships no prompt enhancer and no
    # duration head (both load as None) and this server uses neither: prompt
    # structure belongs to the client's compiler.
    if getattr(pipe, "prompt_enhancer", None) is not None or getattr(pipe, "duration_head", None) is not None:
        pipe.register_modules(prompt_enhancer=None, duration_head=None)
    pipe.to("cuda")

    try:
        _quant = _setup_fp8(pipe)
    except Exception as exc:
        logger.warning("fp8 quantisation failed (%s); running in bf16", exc)
    # Regional compile of the DiT's transformer blocks; the warm-up pays for
    # the compilation. Room for the recompiles the audio stream needs.
    from torch import _dynamo
    _dynamo.config.cache_size_limit = max(_dynamo.config.cache_size_limit, 64)
    pipe.transformer.compile_repeated_blocks(fullgraph=True)
    _compiled = True

    if getattr(pipe, "vocoder", None) is not None:
        _audio_sample_rate = getattr(pipe.vocoder.config, "output_sampling_rate", _audio_sample_rate)

    logger.info("Loaded in %.1fs: %dx%d @%.0ffps, audio@%dHz, %.1f GB VRAM, quant=%s",
                time.monotonic() - t0, WIDTH, HEIGHT, FRAME_RATE, _audio_sample_rate,
                torch.cuda.memory_allocated() / 1e9, _quant)

    # Compilation, kernel autotuning and the CPU warm-up of a fresh box
    # happen here, not on a viewer's clip. Out of memory turns on VAE
    # tiling; a compiler that cannot run this model is dropped.
    t1 = time.monotonic()
    for _attempt in range(3):
        try:
            _warmup()
            break
        except Exception as exc:
            session.clear()
            torch.cuda.empty_cache()
            if isinstance(exc, torch.OutOfMemoryError) and not getattr(pipe.vae, "use_tiling", False):
                logger.warning("Warm-up ran out of memory (%s); enabling VAE tiling", str(exc).split(".")[0])
                pipe.vae.enable_tiling()
            elif _compiled:
                logger.warning("Warm-up failed under torch.compile (%s); running the blocks eagerly", exc)
                _uncompile(pipe)
                _compiled = False
            else:
                raise
    logger.info("Warm-up done in %.0fs", time.monotonic() - t1)
    _ready = True
    logger.info("Ready: serving /generate (quant=%s, compiled=%s)", _quant, _compiled)


# Layers left in bf16: the small in/out projections, the caption projection
# (a few hundred tokens), the timestep embedding, norms and gates. The K/V
# projections of the cross attentions see the caption or the other
# modality's few tokens, where quantising the activations costs more than
# the matmul saves (the min(M, K, N) >= 1024 rule of the torchao recipe).
_QUANT_SKIP = ("proj_in", "proj_out", "caption_projection", "time_embed", "norm", "to_gate_logits", "scale_shift")


def _quant_filter(module, fqn: str) -> bool:
    if not isinstance(module, torch.nn.Linear):
        return False
    if min(module.in_features, module.out_features) < 1024:
        return False
    if any(skip in fqn for skip in _QUANT_SKIP):
        return False
    if fqn.endswith(("to_k", "to_v")) and any(x in fqn for x in ("attn2", "to_audio_attn", "to_video_attn")):
        return False
    return True


def _setup_fp8(p) -> str:
    """fp8 matmuls (dynamic activations and weights, per row) on the DiT's
    large Linear layers: where the speed comes from on a compute-bound
    model (weight-only fp8 saves memory, not time)."""
    from torchao.quantization import Float8DynamicActivationFloat8WeightConfig, PerRow, quantize_

    n_layers = sum(1 for name, m in p.transformer.named_modules() if _quant_filter(m, name))
    before = torch.cuda.memory_allocated() / 1e9
    t0 = time.monotonic()
    quantize_(p.transformer, Float8DynamicActivationFloat8WeightConfig(granularity=PerRow()),
              filter_fn=_quant_filter)
    torch.cuda.empty_cache()
    logger.info("Quantised %d Linear layers of the DiT to fp8 in %.0fs (%.1f → %.1f GB resident)",
                n_layers, time.monotonic() - t0, before, torch.cuda.memory_allocated() / 1e9)
    return "fp8"


def _uncompile(p) -> None:
    """Undo compile_repeated_blocks: nn.Module.compile() keeps the eager
    path and stores the compiled one on the instance."""
    import torch._dynamo  # noqa: F401

    names = set(getattr(p.transformer, "_repeated_blocks", []) or [])
    for m in p.transformer.modules():
        if m.__class__.__name__ in names and getattr(m, "_compiled_call_impl", None) is not None:
            m._compiled_call_impl = None
    torch._dynamo.reset()


# A story-agnostic warm-up sentence: compilation and autotuning depend on
# the shape, not the words.
WARMUP_PROMPT = ("A person stands in a softly lit, plain room and slowly raises one hand, "
                 "then lowers it. The camera remains static.")


def _warmup() -> None:
    """Throwaway renders at the configured frame counts on a flat reference."""
    frames_list = [int(x) for x in os.getenv("LTX2_WARMUP_FRAMES", "153,153,153").split(",") if x.strip()]
    ref = Image.new("RGB", (WIDTH, HEIGHT), (235, 235, 235))
    for n in frames_list:
        session.clear()
        session.update(ref_image=ref, window=None, turn_count=0, tails={})
        t0 = time.monotonic()
        _run_pipeline(GenerateRequest(prompt=WARMUP_PROMPT, num_frames=n, seed=1))
        _warm_frames.add(_snap_to_frame_grid(n))
        logger.info("Warm-up: %d frames in %.1fs", n, time.monotonic() - t0)
    session.clear()


# ── Endpoints ─────────────────────────────────────────────────────

@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate one clip. Returns an MP4 with LTX-2.5's native audio."""
    if pipe is None:
        raise HTTPException(503, "Pipeline still loading — try again shortly")
    if not _ready:
        raise HTTPException(503, "Server warming up — try again shortly")
    # The lock covers the GPU only: the next clip's forwards may start
    # while this one's frames are being encoded on the CPU.
    async with _lock:
        result = await asyncio.to_thread(_run_pipeline, req)
    return await asyncio.to_thread(_encode_response, result)


@app.get("/health")
async def health():
    return {
        "status": "ready" if _ready else ("warming" if pipe is not None else "loading"),
        "model": MODEL_ID,
        "frame_rate": FRAME_RATE,
        "warm_frames": sorted(_warm_frames),
        "width": WIDTH,
        "height": HEIGHT,
        "quant": _quant,
        "compiled": _compiled,
        "audio_ceiling_lufs": AUDIO_CEILING_LUFS,
        "session_epoch": SESSION_EPOCH,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "turn_count": session.get("turn_count", 0),
        "has_reference": "ref_image" in session,
        "tail_ids": sorted(session.get("tails", {}).keys()),
        "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 1) if torch.cuda.is_available() else None,
    }


# ── Generation ────────────────────────────────────────────────────

def _run_pipeline(req: GenerateRequest) -> dict:
    """The GPU part of a request: session bookkeeping, the pipeline call,
    the sliding window. Returns the frames (uint8 and YUV), the audio and
    the timings; the encode happens outside the lock in `_encode_response`."""
    from diffusers.pipelines.ltx2.pipeline_ltx2_condition import LTX2VideoCondition

    if req.start:
        if req.reference_image is None and not req.window_frames:
            raise HTTPException(400, "a new session needs reference_image or window_frames")
        session.clear()
        session.update(window=None, turn_count=0, tails={})
        logger.info("New session from %s", "the client's frames" if req.window_frames else "the reference image")
    elif "turn_count" not in session:
        raise HTTPException(400, "no session: start one with reference_image or window_frames "
                                 "(send it again if the server was restarted)")
    if req.reference_image is not None:
        session["ref_image"] = _decode_image(req.reference_image)

    if req.window_frames:
        frames = [np.asarray(_decode_image(f).resize((WIDTH, HEIGHT))) for f in req.window_frames]
        session["window"] = np.stack(frames).astype(np.uint8)
        logger.info("Window from %d frames of the client's copy", len(frames))
    elif req.clip_id is not None and req.parent_clip_id is not None:
        # Fenced render: resolve the parent's tail from the keyed store.
        tails: dict = session["tails"]
        if req.parent_clip_id in tails:
            session["window"] = tails[req.parent_clip_id]
        elif req.parent_clip_id != 0 and session.get("window") is not None:
            raise HTTPException(
                409,
                f"unknown_parent: parent_clip_id={req.parent_clip_id} not among the held tails "
                f"{sorted(tails)}; resend with window_frames from the client's copy",
            )
    if session["window"] is None and "ref_image" not in session:
        raise HTTPException(400, "nothing to continue from: start the session with reference_image")

    session["turn_count"] += 1
    turn = session["turn_count"]
    num_frames = _snap_to_frame_grid(req.num_frames)

    # Exactly one condition at index 0: the reference (the first clip) or
    # the previous clip's tail. Two index-0 conditions would overwrite each
    # other's leading tokens.
    if session["window"] is None:
        conditions = [LTX2VideoCondition(frames=session["ref_image"], index=0, strength=1.0)]
        cond_desc = "reference image"
    else:
        conditions = [LTX2VideoCondition(frames=session["window"], index=0, strength=req.continuity_strength)]
        cond_desc = f"{session['window'].shape[0]} trailing frames"

    seed = req.seed if req.seed is not None else int.from_bytes(os.urandom(4), "big")
    generator = torch.Generator(device="cuda").manual_seed(seed)

    logger.info("Turn %d: %d frames%s, %d prompt words, on %s (strength=%.2f, seed=%d) …",
                turn, num_frames, "" if num_frames in _warm_frames else " (NOT WARMED: compiles now)",
                len(req.prompt.split()), cond_desc, req.continuity_strength, seed)
    t0 = time.monotonic()
    call_kwargs: dict = dict(
        conditions=conditions,
        prompt=req.prompt,
        height=HEIGHT,
        width=WIDTH,
        num_frames=num_frames,
        frame_rate=FRAME_RATE,
        generator=generator,
        output_type="pt",   # a GPU tensor: the frames are converted there
        return_dict=False,
        **_call_defaults,
    )
    try:
        video, audio = pipe(**call_kwargs)
    except torch.OutOfMemoryError as exc:
        # A long clip's untiled VAE decode can exceed what is left next to
        # the resident models. Tile from now on and retry once.
        if getattr(pipe.vae, "use_tiling", False):
            raise
        logger.warning("Turn %d ran out of memory (%s); enabling VAE tiling and retrying",
                       turn, str(exc).split(".")[0])
        torch.cuda.empty_cache()
        pipe.vae.enable_tiling()
        video, audio = pipe(**call_kwargs)
    dt = time.monotonic() - t0

    # uint8 [0, 255] once: the tail is the next clip's condition (the
    # pipeline maps conditions with `pixels / 127.5 - 1.0`), and the encoder
    # pipes the same frames as planar YUV, converted on the GPU.
    t_post = time.monotonic()
    frames_u8, on_device = encode.frames_to_uint8(video, keep_on_device=True)
    yuv = encode.yuv420p_planes(on_device)
    post_s = time.monotonic() - t_post
    tail = min(req.continuity_frames, frames_u8.shape[0])
    session["window"] = frames_u8[-tail:]

    # Fenced tails: store under clip_id, keep the last 3.
    if req.clip_id is not None:
        tails = session.setdefault("tails", {})
        tails[req.clip_id] = frames_u8[-tail:]
        for k in sorted(tails)[:-3]:
            del tails[k]

    logger.info("Turn %d: generated in %.2fs, frames converted in %.2fs, %s, %.1f GB VRAM",
                turn, dt, post_s, list(frames_u8.shape), torch.cuda.memory_allocated() / 1e9)

    audio_arg = None
    if audio is not None:
        a = audio[0] if getattr(audio, "ndim", 0) > 2 else audio
        audio_arg = torch.from_numpy(a).float() if isinstance(a, np.ndarray) else a.float().cpu()

    return {"frames": frames_u8, "yuv": yuv, "audio": audio_arg, "turn": turn, "latency": dt, "post": post_s}


def _encode_response(r: dict) -> FileResponse:
    frames_u8, audio_arg, turn, dt = r["frames"], r["audio"], r["turn"], r["latency"]
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()

    t1 = time.monotonic()
    audio_meta = encode.encode_mp4(frames_u8, int(FRAME_RATE), audio_arg, _audio_sample_rate, tmp.name,
                                   yuv=r["yuv"], threads=X264_THREADS, ceiling_lufs=AUDIO_CEILING_LUFS)
    encode_s = time.monotonic() - t1

    duration_s = frames_u8.shape[0] / FRAME_RATE
    logger.info("Turn %d: encoded in %.2fs, %.2fs clip → %s", turn, encode_s, duration_s, tmp.name)

    return FileResponse(
        tmp.name,
        media_type="video/mp4",
        filename=f"turn_{turn:04d}.mp4",
        headers={
            "X-Turn": str(turn),
            "X-Session-Epoch": SESSION_EPOCH,
            "X-Latency-S": f"{dt:.2f}",
            "X-Encode-S": f"{encode_s:.2f}",
            "X-Post-S": f"{r['post']:.2f}",
            **{f"X-Audio-{k}": v for k, v in audio_meta.items()},
            "X-Num-Frames": str(frames_u8.shape[0]),
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
    host = os.getenv("LTX2_HOST", "127.0.0.1")
    port = int(os.getenv("LTX2_PORT", "8000"))
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("Listening on %s: the server has no authentication, so anyone who can reach port %d "
                       "can render on this GPU", host, port)
    logger.info("Starting LTX-2.5 server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
