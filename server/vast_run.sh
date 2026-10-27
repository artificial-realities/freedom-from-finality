#!/usr/bin/env bash
# Rent a GPU on Vast.ai, stand up the LTX-2.5 server on it, tunnel it to
# localhost:8000, run the film on the box, and tear everything down.
#
#   server/vast_run.sh check                       keys, credit and ssh key ready? and what renting costs now;
#                                                  rents nothing
#   server/vast_run.sh up [OFFER_ID]               rent (the cheapest matching H100 unless given), install,
#                                                  start the server, open the tunnel, wait until it is ready
#   server/vast_run.sh run [config] [args...]      your story ON the box; its page comes back on localhost:8765
#   server/vast_run.sh pull [local_dir]            bring your story home, and fetch the run's logs
#   server/vast_run.sh restart                     restart the server on the same box and wait until it is ready
#   server/vast_run.sh status                      instance state and server health
#   server/vast_run.sh down                        bring your story home, close the tunnel, destroy the instance
#
# The film runs on the box, but your story lives here: `run` copies it to
# the box first, brings it back every minute and when the film stops, and
# `down` brings it back before the box is destroyed. A copy coming back
# replaces yours only if it is the same story and not behind it
# (`main.py adopt`); the one it replaces is kept as story.previous.
#
# Needs VAST_API_KEY, HF_TOKEN and OPENAI_API_KEY in .env, the `vastai` CLI,
# and your ssh public key registered on Vast (`vastai create ssh-key "$(cat ~/.ssh/id_ed25519.pub)"`).
# VAST_QUERY picks other offers. Renting bills by the hour until `down`.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env here: cp .env.example .env and fill in VAST_API_KEY, HF_TOKEN and OPENAI_API_KEY" >&2; exit 1; }
set -a; . ./.env; set +a
export VAST_API_KEY
# What a command needs before it rents or reaches anything: the vastai CLI and these .env variables.
need() {
  command -v vastai >/dev/null || { echo "the vastai CLI is not installed: pip install vastai" >&2; exit 1; }
  local v; for v in "$@"; do [ -n "${!v:-}" ] || { echo "$v is not set in .env (see .env.example)" >&2; exit 1; }; done
}
STATE=logs/vast; mkdir -p "$STATE"
CONFIG=configs/video.yaml
STORY=story
PY=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
IMAGE=pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel
# H100 SXM and NVL: cheaper than the PCIe on Vast and ~1.3x faster on this compute-bound model.
QUERY=${VAST_QUERY:-'gpu_name in [H100_SXM,H100_NVL] gpu_ram>=80 num_gpus=1 disk_space>=180 inet_down>=800 rentable=true verified=true reliability>0.98'}
DISK=200
# What a fresh box downloads (the image, the packages, the weights). Some
# hosts bill it per GB, and the hourly price leaves it out.
DOWNLOAD_GB=145
WEIGHTS_URL=https://huggingface.co/Lightricks/LTX-2.5-Diffusers

# Cheapest first; with --storage the hourly price counts the disk `up` asks for.
offers() { vastai search offers "$QUERY" --order dph_total --storage "$DISK" --raw; }
pick_offer() {
  offers | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["id"]) if r else sys.exit("no offers match")'
}
# A box that cannot download the gated weights is paid for and useless, so the token is tried first.
need_weights() {
  case "$(curl -s -o /dev/null -w '%{http_code}' -I -L -H "Authorization: Bearer $HF_TOKEN" "$WEIGHTS_URL/resolve/main/model_index.json")" in
    200) echo "HF_TOKEN reaches the LTX-2.5 weights";;
    401) echo "HF_TOKEN is not accepted by Hugging Face: make a read token at https://huggingface.co/settings/tokens" >&2; exit 1;;
    403) echo "HF_TOKEN works, but the licence is not accepted yet: click Agree at $WEIGHTS_URL" >&2; exit 1;;
    *) echo "could not reach Hugging Face to try HF_TOKEN" >&2;;
  esac
}
instance_json() { vastai show instance "$(cat "$STATE/id")" --raw; }
field() { instance_json | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1') or '')"; }
ssh_cmd() { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")" "$@"; }
box_rsync() { rsync -a --exclude .lock -e "ssh -p $(cat "$STATE/port") -o StrictHostKeyChecking=no -o ConnectTimeout=15" "$@"; }

# Bring the box's copy of the story home: it lands next to yours and
# replaces it only if it is the same story and not behind. Fails when the
# box cannot be reached or the copy is refused; nothing on the box is fine.
pull_story() {
  local code=0
  ssh_cmd "test -f /root/film/$STORY/story.json" || code=$?
  [ "$code" = 1 ] && return 0
  [ "$code" = 0 ] || { echo "the box cannot be reached; your story was not brought home" >&2; return "$code"; }
  rm -rf "$STORY.incoming"
  # Yours is the basis for the transfer: only what changed crosses the
  # network (the logs that grow are sent as their new lines). Copied, not
  # linked, so the story that is replaced keeps files of its own.
  local basis=()
  [ -d "$STORY" ] && basis=(--copy-dest "$PWD/$STORY")
  box_rsync ${basis[@]+"${basis[@]}"} "root@$(cat "$STATE/host"):/root/film/$STORY/" "$STORY.incoming/"
  "$PY" main.py adopt "$STORY.incoming" --story "$STORY"
}
# Your story to the box, so the film there resumes it (it begins a new one
# only when you have none).
push_story() {
  [ -f "$STORY/story.json" ] || return 0
  ssh_cmd "mkdir -p /root/film"
  box_rsync --delete "$STORY/" "root@$(cat "$STATE/host"):/root/film/$STORY/"
  echo "your story is on the box"
}
# The frame counts the server warms come from the client's clip shape, so they are never typed twice.
server_env() { echo "HF_TOKEN=$HF_TOKEN LTX2_WARMUP_FRAMES=$(python3 -W ignore::RuntimeWarning -m src.i2v.shape "$CONFIG")"; }

# Everything `up` and `run` need, and what renting costs right now. Rents nothing.
check() {
  need VAST_API_KEY HF_TOKEN OPENAI_API_KEY
  vastai show user --raw | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    d = {"error": True}
if d.get("error"):
    sys.exit("Vast said no: check VAST_API_KEY (https://cloud.vast.ai/manage-keys/) and your connection")
print("VAST_API_KEY works; credit on the account: $%.2f" % (d.get("credit") or 0))'
  vastai show ssh-keys --raw | python3 -c '
import glob, json, os, sys
registered = " ".join(k.get("public_key") or "" for k in json.load(sys.stdin))
mine = [open(p).read().split()[1:2] for p in glob.glob(os.path.expanduser("~/.ssh/*.pub"))]
if not any(k and k[0] in registered for k in mine):
    sys.exit("none of your ssh keys is on Vast: add ~/.ssh/id_ed25519.pub under SSH Keys at https://cloud.vast.ai/manage-keys/ (ssh-keygen -t ed25519 makes one)")
print("your ssh key is on Vast")'
  need_weights
  echo "OPENAI_API_KEY is set (not tried: a call costs money)"
  # The estimate counts 15 min of startup, and OpenAI at about $0.50 per hour of film.
  offers | python3 -c '
import json, sys
r = json.load(sys.stdin)
if not r:
    sys.exit("no offers match right now: try again later, or set VAST_QUERY")
o, gb = r[0], float(sys.argv[1])
hour, per_gb = o["dph_total"], o.get("inet_down_cost") or 0
download = gb * per_gb
print("\nup would rent offer %s: %s in %s" % (o["id"], o["gpu_name"], o.get("geolocation") or "?"))
print("  the box    $%.2f an hour, from up until down" % hour)
print("  download   $%.2f once per box (about %d GB at $%.3f a GB)" % (download, gb, per_gb))
print("  OpenAI     about $0.50 an hour of film")
for label, h in (("30 min", 0.5), ("1 hour", 1.0), ("2 hours", 2.0)):
    print("  %-7s of film, startup included: about $%.2f" % (label, (h + 0.25) * hour + download + 0.5 * h))' "$DOWNLOAD_GB"
}
up() {
  need VAST_API_KEY HF_TOKEN
  need_weights
  local offer=${1:-$(pick_offer)}
  echo "renting offer $offer"
  vastai create instance "$offer" --image "$IMAGE" --disk "$DISK" --ssh --direct --label ltx25 --raw \
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
  scp -o StrictHostKeyChecking=no -P "$(cat "$STATE/port")" server/ltx2_server.py server/encode.py server/audio_ceiling.py server/remote_setup.sh server/requirements.txt "root@$(cat "$STATE/host"):/root/"
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
  need VAST_API_KEY HF_TOKEN
  # Same box, new server process; the weights are cached there, so this is load time only (a few minutes).
  scp -o StrictHostKeyChecking=no -P "$(cat "$STATE/port")" server/ltx2_server.py server/encode.py server/audio_ceiling.py "root@$(cat "$STATE/host"):/root/"
  # Kill and start in separate ssh calls: with both in one command line the
  # kill pattern matches the shell running it (its argv holds the start command).
  ssh_cmd "pkill -f '[l]tx2_server.py' || true; sleep 2"
  ssh_cmd "cd /root && setsid -f env $(server_env) python ltx2_server.py > server.log 2>&1 < /dev/null"
  echo "server restarting; waiting until it is ready (load + warm-up)"
  wait_ready 80 || exit 1
}
run() {
  need OPENAI_API_KEY
  # The film runs on the GPU box next to the server, so no clip crosses the
  # internet (an MP4 through the tunnel cost 1.5-4.6 s per clip). The working
  # tree goes over with git archive (tracked files, committed or not), the
  # OpenAI key from .env, and the film page comes back through ssh on localhost:8765.
  # Extra arguments reach main.py (e.g. --clips 8). Ctrl+C ends the session.
  local config=${1:-$CONFIG}; shift $(( $# > 0 ? 1 : 0 ))
  local tree; tree=$(git stash create); git archive --format=tar "${tree:-HEAD}" | ssh_cmd "mkdir -p /root/film && tar -x -C /root/film"
  # Only the client's key goes over (the box is someone else's machine).
  grep -E '^OPENAI_API_KEY=' .env | ssh_cmd "cat > /root/film/.env && chmod 600 /root/film/.env"
  ssh_cmd "cd /root/film && pip install -q -r requirements.txt 2>&1 | grep -v 'WARNING: Running pip' | tail -1"
  # Anything a run before this one left on the box comes home first (a copy
  # behind yours is refused, and yours replaces it); then yours goes over.
  # Without it on the box the film would begin anew.
  pull_story || true
  push_story
  ( while sleep 60; do pull_story >/dev/null 2>&1 || true; done ) &
  local syncer=$!
  trap 'kill '"$syncer"' 2>/dev/null; wait '"$syncer"' 2>/dev/null; pull_story && echo "your story is home"' EXIT
  echo "your story runs on the box: open http://127.0.0.1:8765/  (Ctrl+C ends the session; fetch its logs with: $0 pull)"
  ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -L 8765:localhost:8765 -p "$(cat "$STATE/port")" "root@$(cat "$STATE/host")" \
    "cd /root/film && python main.py play --config $config $*"
}
pull() {
  # Bring the story home, and fetch the box's logs (transcripts, timing logs).
  pull_story
  local local_dir=${1:-logs/box}
  mkdir -p "$local_dir"
  rsync -az -e "ssh -p $(cat "$STATE/port") -o StrictHostKeyChecking=no" "root@$(cat "$STATE/host"):/root/film/logs/" "$local_dir/"
  echo "pulled /root/film/logs → $local_dir"
}
status() {
  [ -f "$STATE/id" ] && instance_json | python3 -c "import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in ['id','actual_status','gpu_name','dph_total','ssh_host','ssh_port']})" || echo "no instance recorded"
  curl -s -m 5 localhost:8000/health || echo "no server on localhost:8000"; echo
}
down() {
  need VAST_API_KEY
  if [ -f "$STATE/id" ] && ! pull_story; then
    read -r -p "Your story could not be brought home from the box. Destroy the box anyway? [y/N] " answer
    [ "$answer" = y ] || { echo "the box keeps running (and billing): try $0 pull, then $0 down" >&2; exit 1; }
  fi
  pkill -f "ssh -f -N .* -L 8000:localhost:8000" 2>/dev/null || true
  if [ -f "$STATE/id" ]; then vastai destroy instance "$(cat "$STATE/id")" -y; rm -f "$STATE/id" "$STATE/host" "$STATE/port"; fi
  echo "tunnel closed, instance destroyed"
}
case "${1:-}" in
  up) shift; up "$@";; check) check;; run) shift; run "$@";; pull) shift; pull "$@";; restart) restart;; status) status;; down) down;;
  *) sed -n '2,23p' "$0"; exit 1;;
esac
