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

The prompt enhancer is not part of the Diffusers pack (it loads as None)
and is intentionally unused: prompt structure is owned by the application
(src/story/compiler.py), not by a rewriter in the server.

Environment variables:
    HF_TOKEN          HuggingFace token (required — the weights are gated)
    LTX2_MODEL        model id (default: Lightricks/LTX-2.5-Diffusers)
    LTX2_VARIANT      "distilled" (default) or "full"
    LTX2_STEPS        full variant only: sampling steps (default 30)
    LTX2_GUIDANCE     full variant only: override guidance_scale
    LTX2_STG          full variant only: override stg_scale
    LTX2_WIDTH        output width, multiple of 32 (default: 960)
    LTX2_HEIGHT       output height, multiple of 32 (default: 544)
    LTX2_OFFLOAD      "cpu" (every component offloaded between uses), "text"
                      (only the text encoder offloaded; DiT resident, ~76 GB
                      peak), "sequential", or "gpu"/unset (everything resident;
                      ~72 GB + activations)
    LTX2_VAE_TILING   "1" to enable VAE tiling (lower peak VRAM on decode)
    LTX2_HOST/PORT    bind address (default: 0.0.0.0:8000)

Speed (docs/reviews/2026-09-16-real-time-plan.md):
    LTX2_ATTN         attention backend for the DiT: "" (PyTorch SDPA, default), "fa3"
                      (FlashAttention-3 from the Hub, Hopper), "fa4" (Blackwell), "sage",
                      "cudnn", or any diffusers backend name. Falls back to SDPA if the
                      warm-up fails on it.
    LTX2_QUANT        "" (bf16, default), "fp8" (torchao float8 dynamic activations and
                      weights, per row; Hopper and later), "mxfp8" / "nvfp4" (torchao
                      microscaling; Blackwell only). Only the DiT's large Linear layers
                      are quantised; the matmuls then run in the low precision, which
                      is where the speed comes from (weight-only fp8 saves memory, not time).
    LTX2_COMPILE      "1" to torch.compile the DiT's transformer blocks (regional,
                      fullgraph). LTX2_COMPILE_MODE picks the mode (default "default");
                      LTX2_COMPILE_DYNAMIC=1 compiles once for every clip length instead
                      of once per frame count (90-170 s each under fp8). Falls back to
                      eager blocks if the warm-up fails under it.
    LTX2_WARMUP       "1" (default): render throwaway clips at startup at the frame
                      counts in LTX2_WARMUP_FRAMES ("121,145"), so compilation, kernel
                      autotuning and the CPU warm-up never land on a viewer's clip.
                      /health reports "warming" until it is done and /generate says 503;
                      then `warm_frames` lists the frame counts warmed. The client
                      refuses to start when a shape it will render is not listed
                      (RUN-08); `python -m src.i2v.shape <config>` prints the list a
                      config needs, and server/vast_run.sh passes it.
    LTX2_ENCODER      "auto" (default: h264_nvenc when ffmpeg and the driver support it,
                      else libx264), "x264", "nvenc", or "av" (diffusers' PyAV path).
    LTX2_X264_PRESET  libx264 preset (default "ultrafast").

Render-time headroom (RUN-14; `server/encode.py`), each off until a GPU
session measures it:
    LTX2_GPU_FRAMES   "1": the pipeline returns its frames as a GPU tensor
                      (`output_type="pt"`) and they become uint8 there, the
                      same bytes as the host path, without converting 390 MB
                      of float32 on the CPU after the measured generate time.
                      Default "0" (the numpy path).
    LTX2_PIPE_FORMAT  "rgb24" (default) or "yuv420p": frames reach ffmpeg as
                      planar YUV 4:2:0 converted beside them (on the GPU with
                      LTX2_GPU_FRAMES=1), half the bytes and no conversion in
                      ffmpeg; BT.601 limited range, as ffmpeg's own. Needs
                      LTX2_GPU_FRAMES=1 (on the host it costs seconds).
    LTX2_X264_THREADS libx264 threads (default 0 = ffmpeg's choice, which on
                      a 128-thread host spawns far more than a 640x352 clip
                      can use).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import shutil
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

import encode

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
_ready: bool = False        # True once the warm-up has run; /generate refuses until then
_warm_frames: set[int] = set()   # frame counts warmed at startup (on the 8k+1 grid), reported by /health
_attn_backend: str = "native"
_quant: str = "bf16"
_compiled: bool = False
_encoder: str = "x264"
# RUN-14 settings (see the docstring); off by default until measured.
_gpu_frames: bool = os.getenv("LTX2_GPU_FRAMES", "0").strip() == "1"
_pipe_format: str = os.getenv("LTX2_PIPE_FORMAT", "rgb24").strip().lower() or "rgb24"
_x264_threads: int = int(os.getenv("LTX2_X264_THREADS", "0") or 0)
if _pipe_format not in ("rgb24", "yuv420p"):
    raise SystemExit(f"LTX2_PIPE_FORMAT must be rgb24 or yuv420p, not {_pipe_format!r}")
if _pipe_format == "yuv420p" and not _gpu_frames:
    # On the host the conversion costs seconds per clip (1.3-6.8 s measured), more than it saves.
    raise SystemExit("LTX2_PIPE_FORMAT=yuv420p needs LTX2_GPU_FRAMES=1 (the planes are made on the GPU)")

# Session: the reference image (PIL, seeds turn 1 and anchors) and the
# tail of the last generated chunk (uint8 ndarray, shape (F, H, W, 3)).
session: dict = {}
# This process's identity: a restart gives a new one (/health, X-Session-Epoch),
# so a client can tell a restarted server from a reset session (RUN-10).
SESSION_EPOCH = os.urandom(4).hex()
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
    # RUN-10's arm (X-11 only): after a restart, the client's copy of the last
    # clip's tail (base64 PNGs); the session continues from it instead of the
    # reference, so costume and position survive the restart.
    window_frames: list[str] | None = None


# ── Startup ───────────────────────────────────────────────────────

@app.on_event("startup")
async def load_pipeline() -> None:
    global pipe, _encode_video, _negative_default, _call_defaults, _steps
    global _audio_sample_rate, _ready, _attn_backend, _quant, _compiled, _encoder

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
            logger.info("Negative prompts are not encoded on the distilled recipe (guidance_scale 1.0): "
                        "a request's negative_prompt has no effect")
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
    elif offload == "text":
        # Keep the DiT and the decoders resident on the GPU and hook only
        # the text side, so the 24 GB Gemma encoder is on the GPU only
        # while it encodes (offloaded when the connectors run after it).
        # Saves the transformer's 44 GB round trip on every clip. The
        # pipeline's end-of-call hook reset would move everything back to
        # the CPU, so it is disabled; accelerate's hooks persist across calls.
        pipe.model_cpu_offload_seq = "prompt_enhancer->text_encoder->connectors"
        pipe._exclude_from_cpu_offload = ["duration_head", "transformer", "vae", "audio_vae", "vocoder"]
        pipe.enable_model_cpu_offload(device="cuda:0")
        pipe.maybe_free_model_hooks = lambda: None
    else:
        # Everything resident ("gpu" or unset). The Diffusers pack ships no
        # prompt enhancer and no duration head (both load as None) and this
        # server uses neither by decision, so make sure nothing is registered
        # under those names: DiT 40 GB + Gemma 24 GB + connectors 6 GB +
        # decoders 2 GB is about 72 GB on an 80 GB card.
        if getattr(pipe, "prompt_enhancer", None) is not None or getattr(pipe, "duration_head", None) is not None:
            pipe.register_modules(prompt_enhancer=None, duration_head=None)
        pipe.to("cuda")

    if os.getenv("LTX2_VAE_TILING") == "1":
        pipe.vae.enable_tiling()
        logger.info("VAE tiling enabled")

    # ── Speed: attention backend, quantisation, compilation ────────
    _attn_backend = _setup_attention(pipe)
    try:
        _quant = _setup_quant(pipe)
    except Exception as exc:
        logger.warning("Quantisation %s failed (%s); running in bf16", os.getenv("LTX2_QUANT"), exc)
        _quant = "bf16"
    _compiled = _setup_compile(pipe)

    _install_stage_timers(pipe)

    if getattr(pipe, "vocoder", None) is not None:
        _audio_sample_rate = getattr(
            pipe.vocoder.config, "output_sampling_rate", _audio_sample_rate,
        )
    _encoder = _pick_encoder()

    vram = torch.cuda.memory_allocated() / 1e9 if torch.cuda.is_available() else 0
    logger.info(
        "Loaded in %.1fs — %s, %d steps, recipe=%s, %dx%d @%.0ffps, audio@%dHz, %.1f GB VRAM, "
        "attention=%s, quant=%s, compiled=%s, encoder=%s",
        time.monotonic() - t0, VARIANT, _steps,
        {k: v for k, v in _call_defaults.items() if k != "sigmas"},
        WIDTH, HEIGHT, FRAME_RATE, _audio_sample_rate, vram,
        _attn_backend, _quant, _compiled, _encoder,
    )

    # ── Warm-up ────────────────────────────────────────────────────
    # Compilation, kernel autotuning and the CPU warm-up of a fresh box
    # (10-20 s on each of the first clips) happen here, not on a viewer's
    # clip. A backend or the compiler that cannot run this model is
    # dropped here too, so a bad flag degrades to the default, not to a
    # dead server.
    if os.getenv("LTX2_WARMUP", "1") == "1":
        t1 = time.monotonic()
        for _attempt in range(3):
            try:
                _warmup()
                break
            except Exception as exc:
                session.clear()
                torch.cuda.empty_cache()
                if isinstance(exc, torch.OutOfMemoryError) and not getattr(pipe.vae, "use_tiling", False):
                    logger.warning("Warm-up ran out of memory (%s); enabling VAE tiling",
                                   str(exc).split(".")[0])
                    pipe.vae.enable_tiling()
                elif _attn_backend != "native":
                    logger.warning("Warm-up failed on attention backend %s (%s); using PyTorch SDPA",
                                   _attn_backend, exc)
                    pipe.transformer.set_attention_backend("native")
                    _attn_backend = "native"
                elif _compiled:
                    logger.warning("Warm-up failed under torch.compile (%s); running the blocks eagerly", exc)
                    _uncompile(pipe)
                    _compiled = False
                else:
                    raise
        logger.info("Warm-up done in %.0fs", time.monotonic() - t1)
    _ready = True
    logger.info("Ready — serving /generate (attention=%s, quant=%s, compiled=%s, encoder=%s)",
                _attn_backend, _quant, _compiled, _encoder)


# ── Speed setup ───────────────────────────────────────────────────

_ATTN_ALIASES = {
    "fa3": "_flash_3_hub", "fa4": "flash_4_hub", "sage": "sage_hub",
    "cudnn": "_native_cudnn", "flash": "flash_hub", "native": "native",
}


def _setup_attention(p) -> str:
    name = os.getenv("LTX2_ATTN", "").strip()
    if not name or name == "native":
        return "native"
    backend = _ATTN_ALIASES.get(name, name)
    try:
        _route_masked_attention_to_native()
        p.transformer.set_attention_backend(backend)
    except Exception as exc:
        logger.warning("Attention backend %s unavailable (%s); using PyTorch SDPA", backend, exc)
        return "native"
    logger.info("Attention backend: %s for unmasked attention (verified by the warm-up)", backend)
    return backend


def _route_masked_attention_to_native() -> None:
    """The Hub kernels (FlashAttention-3, Sage) refuse an attention mask.
    LTX-2's cross attentions pass one (the caption's padding); the big
    self-attention over the video tokens does not, and that is the one
    worth accelerating. Masked calls go to the default (native) backend,
    unmasked ones to whatever set_attention_backend chose."""
    import diffusers.models.attention_dispatch as ad
    orig = ad.dispatch_attention_fn
    if getattr(orig, "_mask_aware", False):
        return

    def dispatch(*args, **kwargs):
        masked = kwargs.get("attn_mask") is not None or (len(args) > 3 and args[3] is not None)
        if masked:
            kwargs["backend"] = None
        return orig(*args, **kwargs)

    dispatch._mask_aware = True  # type: ignore[attr-defined]
    ad.dispatch_attention_fn = dispatch
    # The transformer module imported the function by name.
    import diffusers.models.transformers.transformer_ltx2 as tl
    if getattr(tl, "dispatch_attention_fn", None) is orig:
        tl.dispatch_attention_fn = dispatch


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


def _setup_quant(p) -> str:
    mode = os.getenv("LTX2_QUANT", "").strip().lower()
    if not mode or mode == "bf16":
        return "bf16"
    from torchao.quantization import quantize_

    if mode == "fp8":
        from torchao.quantization import Float8DynamicActivationFloat8WeightConfig, PerRow
        config = Float8DynamicActivationFloat8WeightConfig(granularity=PerRow())
    elif mode == "mxfp8":
        from torchao.prototype.mx_formats import MXDynamicActivationMXWeightConfig
        kwargs = dict(activation_dtype=torch.float8_e4m3fn, weight_dtype=torch.float8_e4m3fn)
        try:
            from torchao.quantization.quantize_.common import KernelPreference
            kwargs["kernel_preference"] = KernelPreference.AUTO
        except ImportError:
            pass
        config = MXDynamicActivationMXWeightConfig(**kwargs)
    elif mode == "nvfp4":
        from torchao.prototype.mx_formats.inference_workflow import NVFP4DynamicActivationNVFP4WeightConfig
        config = NVFP4DynamicActivationNVFP4WeightConfig(
            use_dynamic_per_tensor_scale=True,
            use_triton_kernel=os.getenv("LTX2_NVFP4_TRITON", "1") == "1",
        )
    else:
        raise SystemExit(f"LTX2_QUANT must be fp8, mxfp8, nvfp4 or empty, got {mode!r}")

    n_layers = sum(1 for name, m in p.transformer.named_modules() if _quant_filter(m, name))
    before = torch.cuda.memory_allocated() / 1e9
    t0 = time.monotonic()
    quantize_(p.transformer, config, filter_fn=_quant_filter)
    torch.cuda.empty_cache()
    logger.info("Quantised %d Linear layers of the DiT to %s in %.0fs (%.1f → %.1f GB resident)",
                n_layers, mode, time.monotonic() - t0, before, torch.cuda.memory_allocated() / 1e9)
    return mode


def _setup_compile(p) -> bool:
    if os.getenv("LTX2_COMPILE", "0") != "1":
        return False
    import torch._dynamo  # noqa: F401  (the config module is not loaded by `import torch`)

    mode = os.getenv("LTX2_COMPILE_MODE", "default")
    # Clip lengths vary (121, 145, 169, 193 frames): the first shape compiles
    # static, the second marks the token dimension dynamic. Leave room for
    # both plus recompiles of the audio stream.
    torch._dynamo.config.cache_size_limit = max(torch._dynamo.config.cache_size_limit, 64)
    kwargs: dict = {"fullgraph": True}
    if mode != "default":
        kwargs["mode"] = mode
    # Under fp8 every clip shape (frame count) compiles separately, 90-170 s
    # each on an H100; dynamic=True compiles once for all shapes.
    if os.getenv("LTX2_COMPILE_DYNAMIC", "0") == "1":
        kwargs["dynamic"] = True
    p.transformer.compile_repeated_blocks(**kwargs)
    logger.info("torch.compile on the DiT's transformer blocks (regional, fullgraph, mode=%s, dynamic=%s); "
                "the warm-up pays for the compilation", mode, kwargs.get("dynamic", "auto"))
    return True


def _uncompile(p) -> None:
    """Undo compile_repeated_blocks: nn.Module.compile() keeps the eager
    path and stores the compiled one on the instance."""
    import torch._dynamo  # noqa: F401

    names = set(getattr(p.transformer, "_repeated_blocks", []) or [])
    for m in p.transformer.modules():
        if m.__class__.__name__ in names and getattr(m, "_compiled_call_impl", None) is not None:
            m._compiled_call_impl = None
    torch._dynamo.reset()


# A story-agnostic warm-up sentence (CLEAN-05): compilation and autotuning
# depend on the shape, not the words.
WARMUP_PROMPT = ("A person stands in a softly lit, plain room and slowly raises one hand, "
                 "then lowers it. The camera remains static.")


def _warmup() -> None:
    """Throwaway renders at the configured frame counts on a flat reference."""
    frames_list = [int(x) for x in os.getenv("LTX2_WARMUP_FRAMES", "121,145").split(",") if x.strip()]
    ref = Image.new("RGB", (WIDTH, HEIGHT), (235, 235, 235))
    prompt = WARMUP_PROMPT
    for n in frames_list:
        session["ref_image"] = ref
        session["window"] = None
        session["turn_count"] = 0
        t0 = time.monotonic()
        result = _run_pipeline(GenerateRequest(prompt=prompt, num_frames=n, seed=1, reset_window=True))
        _warm_frames.add(_snap_to_frame_grid(n))
        logger.info("Warm-up: %d frames in %.1fs (%s)", n, time.monotonic() - t0, result["timings"])
    session.clear()


def _pick_encoder() -> str:
    want = os.getenv("LTX2_ENCODER", "auto").strip().lower()
    if want == "av":
        return "av"
    if not _FFMPEG:
        logger.warning("ffmpeg not found; using diffusers' PyAV encoder")
        return "av"
    if want in ("auto", "nvenc"):
        probe = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        probe.close()
        try:
            encode.encode_mp4(np.zeros((9, 160, 256, 3), np.uint8), 24, None, 48000, probe.name, codec="nvenc")
            return "nvenc"
        except Exception as exc:
            level = logger.warning if want == "nvenc" else logger.info
            level("h264_nvenc unavailable (%s); using libx264 %s",
                  str(exc).strip().splitlines()[-1][:160] if str(exc).strip() else exc,
                  os.getenv("LTX2_X264_PRESET", "ultrafast"))
        finally:
            _cleanup(probe.name)
    return "x264"


# ── Endpoints ─────────────────────────────────────────────────────

@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate one chunk. Returns an MP4 with LTX-2.5's native audio."""
    if pipe is None:
        raise HTTPException(503, "Pipeline still loading — try again shortly")
    if not _ready:
        raise HTTPException(503, "Server warming up — try again shortly")
    # The lock covers the GPU only: the next clip's forwards may start
    # while this one's frames are being encoded on the CPU.
    async with _lock:
        result = await asyncio.to_thread(_run_pipeline, req)
    return await asyncio.to_thread(_encode_response, req, result)


@app.post("/reset")
async def reset():
    """Drop session state (reference + window). Keeps the pipeline loaded."""
    session.clear()
    logger.info("Session reset")
    return {"status": "reset"}


@app.get("/health")
async def health():
    return {
        "status": "ready" if _ready else ("warming" if pipe is not None else "loading"),
        "model": MODEL_ID,
        "variant": VARIANT,
        "distilled": VARIANT == "distilled",
        "steps": _steps,
        "recipe": {k: v for k, v in _call_defaults.items() if k != "sigmas"},
        "frame_rate": FRAME_RATE,
        "warm_frames": sorted(_warm_frames),
        "width": WIDTH,
        "height": HEIGHT,
        "anchor": True,
        "attention": _attn_backend,
        "quant": _quant,
        "compiled": _compiled,
        "encoder": _encoder,
        "gpu_frames": _gpu_frames,
        "pipe_format": _pipe_format,
        "x264_threads": _x264_threads,
        # AUD-03: the loudness ceiling this server applies (None = off).
        "session_epoch": SESSION_EPOCH,
        "audio_ceiling_lufs": float(os.environ["LTX2_AUDIO_CEILING_LUFS"])
        if os.getenv("LTX2_AUDIO_CEILING_LUFS", "").strip() else None,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "turn_count": session.get("turn_count", 0),
        "has_reference": "ref_image" in session,
        "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 1)
        if torch.cuda.is_available()
        else None,
    }


# ── Generation ────────────────────────────────────────────────────

def _run_pipeline(req: GenerateRequest) -> dict:
    """The GPU part of a request: session bookkeeping, the pipeline call,
    the sliding window. Returns the frames (uint8), the audio and the
    timings; the encode happens outside the lock in `_encode_response`."""
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
    if req.window_frames and session.get("window") is None and not req.reset_window:
        frames = [np.asarray(_decode_image(f).resize((WIDTH, HEIGHT))) for f in req.window_frames]
        session["window"] = np.stack(frames).astype(np.uint8)
        logger.info("Session window restored from %d frames of the client's last clip (RUN-10)", len(frames))

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
        "Turn %d: %d frames%s, %d prompt words, on %s (strength=%.2f, seed=%d, %d steps) …",
        turn, num_frames, "" if num_frames in _warm_frames else " (NOT WARMED: compiles now)",
        len(req.prompt.split()), cond_desc, req.continuity_strength, seed, _steps,
    )
    t0 = time.monotonic()

    if VARIANT == "distilled" and req.negative_prompt:
        logger.info("Turn %d: negative_prompt ignored (distilled recipe, guidance_scale 1.0)", turn)
    call_kwargs: dict = dict(
        conditions=conditions,
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or _negative_default,
        height=HEIGHT,
        width=WIDTH,
        num_frames=num_frames,
        frame_rate=FRAME_RATE,
        generator=generator,
        output_type="pt" if _gpu_frames else "np",
        return_dict=False,
        **_call_defaults,
    )

    _stage_times.clear()
    _forwards[0] = 0
    try:
        video, audio = pipe(**call_kwargs)
    except torch.OutOfMemoryError as exc:
        # The untiled VAE decode wants ~25 GB for 121 frames and ~40 GB for
        # 193 at 960x544 on this diffusers; a long clip can exceed what is
        # left next to the resident models. Tile from now on and retry once.
        if getattr(pipe.vae, "use_tiling", False):
            raise
        logger.warning("Turn %d ran out of memory (%s); enabling VAE tiling and retrying",
                       turn, str(exc).split(".")[0])
        torch.cuda.empty_cache()
        pipe.vae.enable_tiling()
        _stage_times.clear()
        _forwards[0] = 0
        video, audio = pipe(**call_kwargs)
    dt = time.monotonic() - t0
    logger.info("Turn %d: %s", turn, _stage_summary(dt))

    # uint8 [0, 255] once: the tail is the next turn's condition (the
    # pipeline maps conditions with `pixels / 127.5 - 1.0`, and the
    # single-frame CRF path rejects non-uint8), and the encoder takes the
    # same array. output_type "pt" (LTX2_GPU_FRAMES=1) is a GPU tensor,
    # converted there; "np" is float [0, 1] (B, F, H, W, C) on the host.
    t_post = time.monotonic()
    yuv = None
    try:
        frames_u8, on_device = encode.frames_to_uint8(video, keep_on_device=True)
        if _pipe_format == "yuv420p":
            yuv = encode.yuv420p_planes(on_device)
    except Exception:
        if not torch.is_tensor(video):
            raise
        logger.exception("Turn %d: converting the frames on the GPU failed; converting on the host, piping RGB", turn)
        frames_u8, yuv = encode.frames_to_uint8(video.float().cpu().numpy()), None
    post_s = time.monotonic() - t_post
    tail = min(req.continuity_frames, frames_u8.shape[0])
    session["window"] = frames_u8[-tail:]

    logger.info("Turn %d: generated in %.2fs, frames converted in %.2fs, %s, %.1f GB VRAM",
                turn, dt, post_s, list(frames_u8.shape), torch.cuda.memory_allocated() / 1e9)

    audio_arg = None
    if audio is not None:
        a = audio[0] if getattr(audio, "ndim", 0) > 2 else audio
        audio_arg = (
            torch.from_numpy(a).float() if isinstance(a, np.ndarray)
            else a.float().cpu()
        )

    # Captured under the lock: the next request clears the stage timers.
    return {"frames": frames_u8, "yuv": yuv, "audio": audio_arg, "turn": turn, "latency": dt,
            "post": post_s, "timings": _timings_compact() + f";post={post_s:.2f}"}


def _encode_response(req: GenerateRequest, r: dict) -> FileResponse:
    frames_u8, audio_arg, turn, dt = r["frames"], r["audio"], r["turn"], r["latency"]
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()

    t1 = time.monotonic()
    audio_meta: dict = {}
    if _encoder == "av" or not _FFMPEG:
        # diffusers' PyAV path: libx264 at its defaults, one thread, frame
        # by frame; 17-24 s for a 5 s clip on the Vast boxes.
        _encode_video(
            frames_u8, fps=int(FRAME_RATE), output_path=tmp.name,
            audio=audio_arg, audio_sample_rate=_audio_sample_rate if audio_arg is not None else None,
        )
    else:
        audio_meta = encode.encode_mp4(frames_u8, int(FRAME_RATE), audio_arg, _audio_sample_rate, tmp.name,
                                       codec=_encoder, yuv=r.get("yuv"), threads=_x264_threads)
    encode_s = time.monotonic() - t1

    duration_s = frames_u8.shape[0] / FRAME_RATE
    logger.info("Turn %d: encoded in %.2fs (%s), %.2fs clip → %s", turn, encode_s, _encoder, duration_s, tmp.name)

    return FileResponse(
        tmp.name,
        media_type="video/mp4",
        filename=f"turn_{turn:04d}.mp4",
        headers={
            "X-Turn": str(turn),
            "X-Session-Epoch": SESSION_EPOCH,
            "X-Latency-S": f"{dt:.2f}",
            "X-Encode-S": f"{encode_s:.2f}",
            "X-Post-S": f"{r.get('post', 0.0):.2f}",
            **{f"X-Audio-{k}": v for k, v in audio_meta.items()},
            "X-Timings": r["timings"],
            "X-Num-Frames": str(frames_u8.shape[0]),
            "X-Duration-S": f"{duration_s:.3f}",
            "X-Frame-Rate": str(FRAME_RATE),
            "X-Has-Audio": "1" if audio_arg is not None else "0",
            "X-Variant": VARIANT,
            "X-Encoder": _encoder,
        },
        background=BackgroundTask(_cleanup, tmp.name),
    )


# ── Stage timing ──────────────────────────────────────────────────
# Where a clip's wall time goes: text encoding, condition encoding,
# transformer compute vs. the offload transfers around it, VAE decode,
# audio decode + vocoder. Logged per turn; the remainder is "other".
_stage_times: dict[str, float] = {}
_forwards = [0]


def _timed(obj, attr: str, label: str) -> None:
    fn = getattr(obj, attr)

    def wrapper(*args, **kwargs):
        torch.cuda.synchronize()
        t = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            torch.cuda.synchronize()
            _stage_times[label] = _stage_times.get(label, 0.0) + time.monotonic() - t

    setattr(obj, attr, wrapper)


def _install_stage_timers(p) -> None:
    _timed(p, "encode_prompt", "text")
    _timed(p, "prepare_latents", "conditions")
    if getattr(p, "connectors", None) is not None:
        # Runs after encode_prompt; with offload its hook also moves the
        # text encoder back to the CPU, so that transfer lands here.
        _timed(p.connectors, "forward", "connectors")
    _timed(p.vae, "decode", "vae_decode")
    if getattr(p, "audio_vae", None) is not None:
        _timed(p.audio_vae, "decode", "audio_decode")
    if getattr(p, "vocoder", None) is not None:
        _timed(p.vocoder, "forward", "vocoder")
    # Compute time and forward count: wrap the innermost forward. With CPU
    # offload accelerate keeps the original as `_old_forward` and re-wraps
    # it after every call, so the wrapper survives there; without offload
    # `forward` itself is wrapped.
    tr = p.transformer
    inner_attr = "_old_forward" if hasattr(tr, "_old_forward") else "forward"
    inner = getattr(tr, inner_attr)

    def compute(*args, **kwargs):
        _forwards[0] += 1
        torch.cuda.synchronize()
        t = time.monotonic()
        try:
            return inner(*args, **kwargs)
        finally:
            torch.cuda.synchronize()
            _stage_times["transformer_compute"] = (
                _stage_times.get("transformer_compute", 0.0) + time.monotonic() - t
            )

    setattr(tr, inner_attr, compute)
    # Total (compute plus accelerate's transfers) via torch hooks, which
    # diffusers' hook reset leaves alone.
    marks: dict[str, float] = {}

    def pre(module, args):
        torch.cuda.synchronize()
        marks["t"] = time.monotonic()

    def post(module, args, output):
        torch.cuda.synchronize()
        _stage_times["transformer_total"] = (
            _stage_times.get("transformer_total", 0.0) + time.monotonic() - marks["t"]
        )

    tr.register_forward_pre_hook(pre)
    tr.register_forward_hook(post)


def _timings_compact() -> str:
    """The stage timings as one header-friendly line: key=seconds;..."""
    parts = [f"{k}={v:.2f}" for k, v in _stage_times.items()]
    parts.append(f"forwards={_forwards[0]}")
    return ";".join(parts)


def _stage_summary(total: float) -> str:
    t = _stage_times
    compute = t.get("transformer_compute", t.get("transformer_total", 0.0))
    transfer = max(t.get("transformer_total", 0.0) - compute, 0.0)
    audio = t.get("audio_decode", 0.0) + t.get("vocoder", 0.0)
    accounted = t.get("text", 0.0) + t.get("connectors", 0.0) + t.get("conditions", 0.0) \
        + max(t.get("transformer_total", 0.0), compute) + t.get("vae_decode", 0.0) + audio
    return (f"timings: text {t.get('text', 0.0):.1f}s, connectors {t.get('connectors', 0.0):.1f}s, "
            f"conditions {t.get('conditions', 0.0):.1f}s, "
            f"transformer {compute:.1f}s compute + {transfer:.1f}s transfer over {_forwards[0]} forwards, "
            f"vae decode {t.get('vae_decode', 0.0):.1f}s, audio {audio:.1f}s, "
            f"other {max(total - accounted, 0.0):.1f}s")


# ── Helpers ───────────────────────────────────────────────────────

_FFMPEG = shutil.which("ffmpeg") is not None


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
