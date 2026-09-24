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
# The defaults (fp8, compile, 640x352, warm-up shapes) live in one file, shared with `vast_run.sh restart`.
. /root/server_defaults.sh
nohup python ltx2_server.py > server.log 2>&1 &
echo "server started pid $! (variant=$LTX2_VARIANT offload=$LTX2_OFFLOAD)"
