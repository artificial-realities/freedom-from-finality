#!/bin/bash
# Runs on the GPU box (copied there by server/vast_run.sh): install the
# server's dependencies and start ltx2_server.py in the background.
# Env: HF_TOKEN (required, gated weights), LTX2_OFFLOAD (default "cpu").
set -e
cd /root
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq ffmpeg >/dev/null 2>&1 || true
pip install -q --upgrade pip >/dev/null
pip install -q "git+https://github.com/huggingface/diffusers" "transformers>=4.50" "accelerate>=1.0" \
  "peft>=0.12" "sentencepiece>=0.2" "av>=12.0" "fastapi>=0.110" "uvicorn>=0.29" "hf_transfer" 2>&1 | tail -2
python -c "import diffusers, transformers, torch; print('diffusers', diffusers.__version__, 'transformers', transformers.__version__, 'torch', torch.__version__, 'cuda', torch.cuda.is_available())"
python -c "from diffusers import LTX2ConditionPipeline; print('LTX2ConditionPipeline import ok')"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
echo "cgroup memory limit: $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo unknown)"; free -g | sed -n 2p; df -h /root | tail -1
export HF_HUB_ENABLE_HF_TRANSFER=1
export LTX2_OFFLOAD=${LTX2_OFFLOAD:-cpu}
nohup python ltx2_server.py > server.log 2>&1 &
echo "server started pid $! (offload=$LTX2_OFFLOAD)"
