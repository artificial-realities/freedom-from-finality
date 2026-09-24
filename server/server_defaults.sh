# The LTX-2.5 server's default settings, sourced by remote_setup.sh (first start) and by
# `server/vast_run.sh restart`, so a restart runs the same recipe as the first start unless an
# LTX2_* variable in the environment says otherwise (see ltx2_server.py for every setting).
export HF_HUB_ENABLE_HF_TRANSFER=1
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
export LTX2_OFFLOAD=${LTX2_OFFLOAD:-gpu}
export LTX2_VARIANT=${LTX2_VARIANT:-distilled}
# fp8 matmuls: 1.27x on the forwards on Hopper, measured faster than bf16 end to end on a B200 too; no visible cost on the minifigure.
export LTX2_QUANT=${LTX2_QUANT:-fp8}
# The MVP setting measured on 2026-09-16 (docs/reviews/2026-09-16-real-time-plan.md): compiled blocks, the frame size below,
# and a warm-up of every clip shape the film will render. The frame counts come from the client's shape
# registry (`python -m src.i2v.shape <config>`, which server/vast_run.sh passes in LTX2_WARMUP_FRAMES); the
# default below is the live config's one shape (145 frames) three times, for a start without vast_run.sh.
# /health reports what was warmed and the client refuses to start without its shapes (RUN-08).
export LTX2_COMPILE=${LTX2_COMPILE:-1}
# RUN-17 (D-15, GPU session 4): one standard size, 512x288, exactly 16:9 on LTX's grid of 32: the render
# ratio fell from 0.84 to 0.58 (p95) against 640x352 on the same box, with identity and detail held by eye.
export LTX2_WIDTH=${LTX2_WIDTH:-512}
export LTX2_HEIGHT=${LTX2_HEIGHT:-288}
export LTX2_WARMUP_FRAMES=${LTX2_WARMUP_FRAMES:-145,145,145}
# Render-time headroom (RUN-14, server/encode.py), measured on GPU session 2 (2026-09-24, H100 SXM, 153 frames
# over the 144 aired): wall p95 0.890 on the old path; frames converted on the GPU 0.706; + YUV piped 0.682;
# + 8 x264 threads 0.666 (16: 0.673). NVENC stays unavailable in Vast containers (the video capability is not granted).
export LTX2_GPU_FRAMES=${LTX2_GPU_FRAMES:-1}
export LTX2_PIPE_FORMAT=${LTX2_PIPE_FORMAT:-yuv420p}
export LTX2_X264_THREADS=${LTX2_X264_THREADS:-8}
