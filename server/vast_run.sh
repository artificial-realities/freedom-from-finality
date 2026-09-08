#!/usr/bin/env bash
# Rent a GPU on Vast.ai, stand up the LTX-2.5 server on it, tunnel it to
# localhost:8000, run the client, and tear everything down.
#
#   server/vast_run.sh up [OFFER_ID]                     rent (cheapest matching offer unless given),
#                                                        install, start the server, open the tunnel,
#                                                        wait for /health to say ready
#   server/vast_run.sh run <config> [scenario] [log_dir]  run the client against the tunnel
#   server/vast_run.sh status                            instance state + server health
#   server/vast_run.sh down                              close the tunnel and destroy the instance
#
# Needs VAST_API_KEY and HF_TOKEN in .env, the `vastai` CLI, and your ssh
# public key registered on Vast (`vastai create ssh-key "$(cat ~/.ssh/id_ed25519.pub)"`).
# Override VAST_IMAGE, VAST_QUERY or LTX2_OFFLOAD in the environment if needed.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export VAST_API_KEY
STATE=logs/vast; mkdir -p "$STATE"
IMAGE=${VAST_IMAGE:-pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel}
QUERY=${VAST_QUERY:-'gpu_name in [A100_SXM4,A100_PCIE,H100_SXM,H100_PCIE] gpu_ram>=80 num_gpus=1 disk_space>=180 inet_down>=800 rentable=true verified=true reliability>0.98'}

pick_offer() {
  vastai search offers "$QUERY" --order dph_total --raw \
    | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["id"]) if r else sys.exit("no offers match")'
}
instance_json() { vastai show instance "$(cat "$STATE/id")" --raw; }
field() { instance_json | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1') or '')"; }
ssh_cmd() { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")" "$@"; }

up() {
  local offer=${1:-$(pick_offer)}
  echo "renting offer $offer"
  vastai create instance "$offer" --image "$IMAGE" --disk 200 --ssh --direct --label ar-ltx25 --raw \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("success"), d; print(d["new_contract"])' > "$STATE/id"
  echo "instance $(cat "$STATE/id"): waiting for boot"
  for _ in $(seq 1 60); do [ "$(field actual_status)" = running ] && break; sleep 10; done
  field ssh_host > "$STATE/host"; field ssh_port > "$STATE/port"
  for _ in $(seq 1 30); do ssh_cmd true 2>/dev/null && break; sleep 10; done
  scp -o StrictHostKeyChecking=no -P "$(cat "$STATE/port")" server/ltx2_server.py server/remote_setup.sh "root@$(cat "$STATE/host"):/root/"
  ssh_cmd "chmod +x /root/remote_setup.sh; nohup env HF_TOKEN=$HF_TOKEN LTX2_OFFLOAD=${LTX2_OFFLOAD:-cpu} /root/remote_setup.sh > /root/setup.log 2>&1 &"
  ssh -f -N -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=10 -o ExitOnForwardFailure=yes \
      -L 8000:localhost:8000 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")"
  echo "tunnel open on localhost:8000; waiting for the server (install + weights + load, ~5-15 min)"
  for _ in $(seq 1 120); do
    if curl -s -m 5 localhost:8000/health | grep -q '"ready"'; then echo "server ready"; return; fi
    sleep 15
  done
  echo "server not ready after 30 min. Inspect with: $0 status  and  ssh -p $(cat "$STATE/port") root@$(cat "$STATE/host") tail /root/setup.log /root/server.log"
  exit 1
}
run() { python main.py play --config "${1:?config}" ${2:+--scenario "$2"} --log-dir "${3:-logs/live}"; }
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
  up) shift; up "$@";; run) shift; run "$@";; status) status;; down) down;;
  *) sed -n '2,15p' "$0"; exit 1;;
esac
