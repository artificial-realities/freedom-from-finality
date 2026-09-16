#!/bin/bash
# Runs on the GPU box (copied there by server/vast_run.sh): install the
# server's dependencies and start ltx2_server.py in the background.
# Env: HF_TOKEN (required, gated weights) and any LTX2_* setting (see ltx2_server.py); LTX2_OFFLOAD defaults
# to "gpu" (everything resident on an 80 GB card).
set -e
cd /root
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq ffmpeg rsync >/dev/null 2>&1 || true
pip install -q --upgrade pip >/dev/null
# torchao: fp8 / mxfp8 / nvfp4 matmuls (LTX2_QUANT); kernels: FlashAttention-3/4 and SageAttention from the Hub (LTX2_ATTN).
# torchao tracks torch closely: 0.17+ needs torch 2.10 (it imports torch.nn.functional.ScalingType and takes
# diffusers down with it on import); the 2.9.1 image runs 0.16. Bump both together.
pip install -q "git+https://github.com/huggingface/diffusers" "transformers>=4.50" "accelerate>=1.0" \
  "peft>=0.12" "sentencepiece>=0.2" "av>=12.0" "fastapi>=0.110" "uvicorn>=0.29" "hf_transfer" \
  "torchao<0.17" "kernels>=0.12.3" 2>&1 | tail -2
python -c "import diffusers, transformers, torch, torchao; print('diffusers', diffusers.__version__, 'transformers', transformers.__version__, 'torch', torch.__version__, 'torchao', torchao.__version__, 'cuda', torch.cuda.is_available())"
python -c "from diffusers import LTX2ConditionPipeline; print('LTX2ConditionPipeline import ok')"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_nvenc && echo "ffmpeg has h264_nvenc" || echo "ffmpeg without h264_nvenc (libx264 will be used)"
echo "cgroup memory limit: $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo unknown)"; free -g | sed -n 2p; df -h /root | tail -1
export HF_HUB_ENABLE_HF_TRANSFER=1
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
export LTX2_OFFLOAD=${LTX2_OFFLOAD:-gpu}
export LTX2_VARIANT=${LTX2_VARIANT:-distilled}
# fp8 matmuls: 1.27x on the forwards on Hopper, measured faster than bf16 end to end on a B200 too; no visible cost on the minifigure.
export LTX2_QUANT=${LTX2_QUANT:-fp8}
# The MVP setting measured on 2026-09-16 (docs/reviews/2026-09-16-real-time-plan.md): compiled blocks, 640x352,
# and a warm-up of three 6 s clips (145 frames, the fixed `clip_seconds: 6` of configs/director.live.yaml), so
# the one shape the film uses is compiled before the first viewer clip. Override any of these in the environment.
export LTX2_COMPILE=${LTX2_COMPILE:-1}
export LTX2_WIDTH=${LTX2_WIDTH:-640}
export LTX2_HEIGHT=${LTX2_HEIGHT:-352}
export LTX2_WARMUP_FRAMES=${LTX2_WARMUP_FRAMES:-145,145,145}
nohup python ltx2_server.py > server.log 2>&1 &
echo "server started pid $! (variant=$LTX2_VARIANT offload=$LTX2_OFFLOAD)"
