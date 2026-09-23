#!/usr/bin/env bash
# Rent a GPU on Vast.ai, stand up the LTX-2.5 server on it, tunnel it to
# localhost:8000, run the client, and tear everything down.
#
#   server/vast_run.sh up [OFFER_ID]                     rent (cheapest matching H100 SXM/NVL unless given),
#                                                        install, start the server, open the tunnel,
#                                                        wait for /health to say ready
#   server/vast_run.sh run [config] [log_dir] [args...]   run the film ON the box (web player on localhost:8765)
#   server/vast_run.sh pull [remote_dir] [local_dir]      fetch a run's logs and clips from the box
#   server/vast_run.sh restart                           restart the server on the same box (e.g. with
#                                                        LTX2_VARIANT=full) and wait for /health
#   server/vast_run.sh status                            instance state + server health
#   server/vast_run.sh down                              close the tunnel and destroy the instance
#
# Needs VAST_API_KEY and HF_TOKEN in .env, the `vastai` CLI, and your ssh
# public key registered on Vast (`vastai create ssh-key "$(cat ~/.ssh/id_ed25519.pub)"`).
# Every LTX2_* variable in the environment reaches the server (LTX2_OFFLOAD, LTX2_VARIANT, LTX2_WIDTH/HEIGHT,
# LTX2_ATTN, LTX2_QUANT, LTX2_COMPILE, LTX2_ENCODER, ...; see server/ltx2_server.py). VAST_IMAGE and VAST_QUERY
# pick the image and the offer: a B200 needs the CUDA 12.8 image (the default) and
#   VAST_QUERY='gpu_name=B200 num_gpus=1 disk_space>=180 inet_down>=800 rentable=true verified=true'
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export VAST_API_KEY
STATE=logs/vast; mkdir -p "$STATE"
IMAGE=${VAST_IMAGE:-pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel}
# H100 SXM and NVL: cheaper than the PCIe on Vast and ~1.3x faster on this compute-bound model.
QUERY=${VAST_QUERY:-'gpu_name in [H100_SXM,H100_NVL] gpu_ram>=80 num_gpus=1 disk_space>=180 inet_down>=800 rentable=true verified=true reliability>0.98'}

pick_offer() {
  vastai search offers "$QUERY" --order dph_total --raw \
    | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["id"]) if r else sys.exit("no offers match")'
}
instance_json() { vastai show instance "$(cat "$STATE/id")" --raw; }
field() { instance_json | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1') or '')"; }
ssh_cmd() { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")" "$@"; }
# The server's environment: the token plus every LTX2_* variable set here.
# expandable_segments: without it the allocator fragments around the VAE decode (12 GB reserved but unusable, OOM on a 94 GB card).
# The frame counts the server warms come from the client's shape registry for the config the film will run
# (RUN-08): the client refuses to start without them, so they are never typed twice. LTX2_WARMUP_FRAMES in the
# environment wins; AR_CONFIG picks the config (default the live one).
warm_frames() { python3 -W ignore::RuntimeWarning -m src.i2v.shape "${AR_CONFIG:-configs/director.live.yaml}"; }
server_env() { echo "HF_TOKEN=$HF_TOKEN HF_HUB_ENABLE_HF_TRANSFER=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True LTX2_WARMUP_FRAMES=${LTX2_WARMUP_FRAMES:-$(warm_frames)} $(env | grep '^LTX2_' | grep -v '^LTX2_WARMUP_FRAMES=' | tr '\n' ' ')"; }

up() {
  local offer=${1:-$(pick_offer)}
  echo "renting offer $offer"
  vastai create instance "$offer" --image "$IMAGE" --disk 200 --ssh --direct --label ar-ltx25 --raw \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("success"), d; print(d["new_contract"])' > "$STATE/id"
  echo "instance $(cat "$STATE/id"): waiting for boot"
  for _ in $(seq 1 60); do [ "$(field actual_status)" = running ] && break; sleep 10; done
  # Prefer the direct port on the host's public IP (--direct); the ssh
  # proxy (sshN.vast.ai) can refuse the key for minutes after boot.
  instance_json | python3 -c '
import json, sys
d = json.load(sys.stdin)
direct = (d.get("ports") or {}).get("22/tcp") or []
if d.get("public_ipaddr") and direct:
    print(d["public_ipaddr"]); print(direct[0]["HostPort"])
else:
    print(d["ssh_host"]); print(d["ssh_port"])
' | { read -r h; read -r p; echo "$h" > "$STATE/host"; echo "$p" > "$STATE/port"; }
  echo "ssh target $(cat "$STATE/host"):$(cat "$STATE/port")"
  for _ in $(seq 1 30); do ssh_cmd true 2>/dev/null && break; sleep 10; done
  scp -o StrictHostKeyChecking=no -P "$(cat "$STATE/port")" server/ltx2_server.py server/remote_setup.sh "root@$(cat "$STATE/host"):/root/"
  ssh_cmd "chmod +x /root/remote_setup.sh; nohup env $(server_env) /root/remote_setup.sh > /root/setup.log 2>&1 &"
  ssh -f -N -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=10 -o ExitOnForwardFailure=yes \
      -L 8000:localhost:8000 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")"
  echo "tunnel open on localhost:8000; waiting for the server (install + weights + load + warm-up, ~5-20 min)"
  wait_ready 120 || exit 1
}
wait_ready() {
  for _ in $(seq 1 "$1"); do
    if curl -s -m 5 localhost:8000/health | grep -q '"ready"'; then
      echo "server ready: $(curl -s -m 5 localhost:8000/health)"; return 0
    fi
    sleep 15
  done
  echo "server not ready. Inspect with: $0 status  and  ssh -p $(cat "$STATE/port") root@$(cat "$STATE/host") tail /root/setup.log /root/server.log"
  return 1
}
restart() {
  # Same box, new server process: switch any LTX2_* setting (variant, size, attention, quant, compile) without re-renting.
  # The weights are cached on the box, so this is load time only (a few minutes).
  scp -o StrictHostKeyChecking=no -P "$(cat "$STATE/port")" server/ltx2_server.py "root@$(cat "$STATE/host"):/root/"
  # Kill and start in separate ssh calls: with both in one command line the
  # kill pattern matches the shell running it (its argv holds the start command).
  ssh_cmd "pkill -f '[l]tx2_server.py' || true; sleep 2"
  ssh_cmd "cd /root && setsid -f env $(server_env) python ltx2_server.py > server.log 2>&1 < /dev/null"
  echo "server restarting ($(env | grep '^LTX2_' | tr '\n' ' ')); waiting for /health (load + warm-up)"
  wait_ready 80 || exit 1
}
run() {
  # The film runs on the GPU box next to the server, so no clip crosses the
  # internet (the MP4 through the tunnel cost 1.5-4.6 s per clip). The
  # committed tree goes over with git archive, the client's keys from .env,
  # and the web player comes back through the ssh session on localhost:8765.
  # Extra arguments reach main.py (e.g. --segments 8). Ctrl+C stops the film.
  local config=${1:-configs/director.live.yaml} log_dir=${2:-logs/live}; shift $(( $# > 2 ? 2 : $# ))
  # The working tree as it is (tracked files, committed or not), so what you have is what runs.
  local tree; tree=$(git stash create); git archive --format=tar "${tree:-HEAD}" | ssh_cmd "mkdir -p /root/ar && tar -x -C /root/ar"
  grep -E '^(OPENAI|LANGFUSE|ELEVENLABS)_' .env | ssh_cmd "cat > /root/ar/.env && chmod 600 /root/ar/.env"
  ssh_cmd "cd /root/ar && pip install -q -r requirements.txt 2>&1 | grep -v 'WARNING: Running pip' | tail -1"
  echo "the film runs on the box: open http://127.0.0.1:8765/  (Ctrl+C stops it; logs stay in /root/ar/$log_dir, fetch them with: $0 pull)"
  ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -L 8765:localhost:8765 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")" \
    "cd /root/ar && python main.py play --config $config --player web --log-dir $log_dir $*"
}
pull() {
  # Fetch a run's logs (transcript, world model, clips) from the box.
  local remote=${1:-logs/live} local_dir=${2:-logs/live-box}
  mkdir -p "$local_dir"
  rsync -az -e "ssh -p $(cat "$STATE/port") -o StrictHostKeyChecking=no" "root@$(cat "$STATE/host"):/root/ar/$remote/" "$local_dir/"
  echo "pulled /root/ar/$remote → $local_dir"
}
status() {
  [ -f "$STATE/id" ] && instance_json | python3 -c "import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in ['id','actual_status','gpu_name','dph_total','ssh_host','ssh_port']})" || echo "no instance recorded"
  curl -s -m 5 localhost:8000/health || echo "no server on localhost:8000"; echo
}
down() {
  pkill -f "ssh -f -N .* -L 8000:localhost:8000" 2>/dev/null || true
  if [ -f "$STATE/id" ]; then vastai destroy instance "$(cat "$STATE/id")" -y; rm -f "$STATE/id" "$STATE/host" "$STATE/port"; fi
  echo "tunnel closed, instance destroyed"
}
case "${1:-}" in
  up) shift; up "$@";; run) shift; run "$@";; pull) shift; pull "$@";; restart) restart;; status) status;; down) down;;
  *) sed -n '2,15p' "$0"; exit 1;;
esac
