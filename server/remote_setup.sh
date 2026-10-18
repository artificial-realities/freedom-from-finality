#!/bin/bash
# Runs on the GPU box (copied there by server/vast_run.sh): install the
# server's dependencies and start ltx2_server.py in the background.
# Env: HF_TOKEN (required, gated weights) and LTX2_WARMUP_FRAMES.
set -e
cd /root
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq ffmpeg rsync >/dev/null 2>&1 || true
pip install -q --upgrade pip >/dev/null
pip install -q -r /root/requirements.txt 2>&1 | tail -2
python -c "import diffusers, transformers, torch, torchao; print('diffusers', diffusers.__version__, 'transformers', transformers.__version__, 'torch', torch.__version__, 'torchao', torchao.__version__, 'cuda', torch.cuda.is_available())"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
nohup python ltx2_server.py > server.log 2>&1 &
echo "server started pid $!"
