# The LTX-2.5 server's default settings, sourced by remote_setup.sh (first start) and by
# `server/vast_run.sh restart`, so a restart runs the same recipe as the first start unless an
# LTX2_* variable in the environment says otherwise (see ltx2_server.py for every setting).
export HF_HUB_ENABLE_HF_TRANSFER=1
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
export LTX2_OFFLOAD=${LTX2_OFFLOAD:-gpu}
export LTX2_VARIANT=${LTX2_VARIANT:-distilled}
# fp8 matmuls: 1.27x on the forwards on Hopper, measured faster than bf16 end to end on a B200 too; no visible cost on the minifigure.
export LTX2_QUANT=${LTX2_QUANT:-fp8}
# Compiled blocks, the frame size below,
# and a warm-up of every clip shape the film will render. The frame counts come from the client's shape
# registry (`python -m src.i2v.shape <config>`, which server/vast_run.sh passes in LTX2_WARMUP_FRAMES); the
# default below is the live config's one shape (145 frames) three times, for a start without vast_run.sh.
# /health reports what was warmed and the client refuses to start without its shapes.
export LTX2_COMPILE=${LTX2_COMPILE:-1}
# One standard size, 512x288, exactly 16:9 on LTX's grid of 32: 31 % faster than 640x352, with identity and
# detail held by eye.
export LTX2_WIDTH=${LTX2_WIDTH:-512}
export LTX2_HEIGHT=${LTX2_HEIGHT:-288}
export LTX2_WARMUP_FRAMES=${LTX2_WARMUP_FRAMES:-145,145,145}
# Render-time headroom (server/encode.py), on an H100 SXM: render wall p95 0.89 of the clip on the old path; frames
# converted on the GPU 0.71; + YUV piped 0.68; + 8 x264 threads 0.67 (16 is slower). NVENC is unavailable in Vast
# containers (the video capability is not granted).
export LTX2_GPU_FRAMES=${LTX2_GPU_FRAMES:-1}
export LTX2_PIPE_FORMAT=${LTX2_PIPE_FORMAT:-yuv420p}
export LTX2_X264_THREADS=${LTX2_X264_THREADS:-8}
