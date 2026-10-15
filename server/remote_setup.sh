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
# One list for this script and a box set up by hand (torch comes with the image; see the file).
pip install -q -r /root/requirements.txt 2>&1 | tail -2
python -c "import diffusers, transformers, torch, torchao; print('diffusers', diffusers.__version__, 'transformers', transformers.__version__, 'torch', torch.__version__, 'torchao', torchao.__version__, 'cuda', torch.cuda.is_available())"
python -c "from diffusers import LTX2ConditionPipeline; print('LTX2ConditionPipeline import ok')"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_nvenc && echo "ffmpeg has h264_nvenc" || echo "ffmpeg without h264_nvenc (libx264 will be used)"
echo "cgroup memory limit: $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo unknown)"; free -g | sed -n 2p; df -h /root | tail -1
# The defaults (fp8, compile, 512x288, warm-up shapes) live in one file, shared with `vast_run.sh restart`.
. /root/server_defaults.sh
nohup python ltx2_server.py > server.log 2>&1 &
echo "server started pid $! (variant=$LTX2_VARIANT offload=$LTX2_OFFLOAD)"
