"""X-11 (decision record §4.1; RUN-10's flag default): does restoring the
window from the last clip's tail beat a reference re-seed after a server
restart? Runs on the GPU box, next to the server.

    python scripts/experiments/x11_restart.py run --out logs/experiments/x11
    python scripts/experiments/x11_restart.py analyse --out logs/experiments/x11      # locally, after the pull

Each arm (`reference`, `window`) renders session A's segments 45-60 with
the live shape (153 frames) from the reference, through the production
client (`LtxvI2V`, derived seeds under one key, so the arms share their
noise), kills the server after clip 10 (segment 54: the princess and the
crown on screen), restarts it (`server_defaults.sh`, the weights from the
cache) and goes on: the client meets the restarted server's "reference
required" and recovers the arm's way. Kept per arm: every clip, the client's
`last_render` per clip, the restart and recovery times.

Criterion (the record): costume and position persist; recovery time
bounded.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import requests  # noqa: E402

from src.i2v.ltxv import LtxvI2V  # noqa: E402
from src.i2v.shape import ClipShape  # noqa: E402
from src.models.story import Story  # noqa: E402

FIRST, COUNT, KILL_AFTER = 45, 16, 10
SERVER_ENV = ("HF_HUB_OFFLINE=1 LTX2_WARMUP_FRAMES=153,153,153 LTX2_AUDIO_CEILING_LUFS=-18")


def _ready(server: str, timeout: float = 900) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if requests.get(f"{server}/health", timeout=5).json().get("status") == "ready":
                return time.monotonic() - t0
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("the restarted server did not become ready")


def _restart(server: str) -> float:
    """Kill the server process and start it again, as a crash and a
    supervisor would; seconds until /health says ready."""
    subprocess.run("pkill -f '[l]tx2_server.py'", shell=True)
    time.sleep(3)
    subprocess.run(f"cd /root && setsid -f env {SERVER_ENV} bash -c '. ./server_defaults.sh && exec python "
                   f"ltx2_server.py' > /root/server-x11.log 2>&1 < /dev/null", shell=True, check=True)
    return _ready(server)


def run(server: str, out: Path, prompts_path: Path) -> None:
    prompts = {c["segment"]: c["prompt"] for c in json.loads(prompts_path.read_text())["clips"]}
    story = Story.from_json(REPO / "stories" / "lego-void")
    shape = ClipShape.overlapped(6, overlap_frames=9)
    for arm in ("reference", "window"):
        folder = out / arm
        folder.mkdir(parents=True, exist_ok=True)
        requests.post(f"{server}/reset", timeout=30)
        client = LtxvI2V(server_url=server, output_dir=folder, seed_key="x11", restart_recovery=arm)
        rows, restart_s = [], None
        for n, seg in enumerate(range(FIRST, FIRST + COUNT), start=1):
            t0 = time.monotonic()
            path = client._synthesize_sync(story.reference_image, prompts[seg], seg, shape)
            wall = time.monotonic() - t0
            rows.append({"clip": n, "segment": seg, "path": Path(path).name if path else None, "wall_s": round(wall, 3),
                         "after_restart": n == KILL_AFTER + 1, **client.last_render})
            print(f"{arm} clip {n:2} seg {seg}: {wall:5.2f} s  reference={client.last_render.get('reference')} "
                  f"restored={client.last_render.get('restored')} epoch={client.last_render.get('session_epoch')}", flush=True)
            if n == KILL_AFTER:
                restart_s = _restart(server)
                print(f"{arm}: server restarted, ready in {restart_s:.1f} s", flush=True)
        (folder / "renders.json").write_text(json.dumps({"arm": arm, "restart_s": restart_s, "rows": rows}, indent=1))


def _frames(path: Path, size=(320, 176)):
    import numpy as np
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.float32)


def analyse(out: Path) -> dict:
    """Per arm: the picture across the restart (clip 10's last aired frame
    against clip 11's first, and each clip's non-white area and colour as a
    stand-in for who is on screen), the recovery time, and a sheet of
    clips 9-13."""
    import numpy as np
    from PIL import Image
    report = {}
    for arm in ("reference", "window"):
        data = json.loads((out / arm / "renders.json").read_text())
        rows = data["rows"]
        clips = [_frames(out / arm / r["path"]) for r in rows]
        a, b = clips[KILL_AFTER - 1], clips[KILL_AFTER]
        join = float(np.abs(a[len(a) - 10] - b[0]).mean())
        steps = [float(np.abs(c[len(c) - 10] - d[0]).mean()) for c, d in zip(clips, clips[1:])]
        normal = [s for i, s in enumerate(steps) if i != KILL_AFTER - 1]

        def content(f):
            mask = f.min(axis=2) < 200
            px = f[mask]
            return {"area": round(float(mask.mean()), 4),
                    "pink": round(float(((px[:, 0] > 180) & (px[:, 1] < 150) & (px[:, 2] > 140)).mean()) if len(px) else 0, 3),
                    "gold": round(float(((px[:, 0] > 180) & (px[:, 1] > 140) & (px[:, 2] < 80)).mean()) if len(px) else 0, 3)}

        report[arm] = {
            "restart_s": data["restart_s"],
            "recovery_wall_s": rows[KILL_AFTER]["wall_s"], "normal_wall_s_median": statistics.median(r["wall_s"] for r in rows[3:]),
            "recovered_by": "window" if rows[KILL_AFTER].get("restored") else ("reference" if rows[KILL_AFTER].get("reseeded") else "?"),
            "join_at_restart": round(join, 2), "join_elsewhere_median": round(statistics.median(normal), 2),
            "before": content(a[len(a) - 10]), "after": content(b[0]), "after_1s": content(b[min(24, len(b) - 1)]),
        }
        rows_img = [np.concatenate([c[i] for i in (0, 48, 96, 143)], axis=1) for c in clips[KILL_AFTER - 2:KILL_AFTER + 3]]
        Image.fromarray(np.concatenate(rows_img, axis=0).astype(np.uint8)).save(out / arm / "sheet_9-13.png")
        print(arm, json.dumps(report[arm]))
    (out / "analysis.json").write_text(json.dumps(report, indent=1))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("run", "analyse"))
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "x11")
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--prompts", type=Path, default=REPO / "logs" / "experiments" / "gpu1" / "probe-prompts.json")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "run":
        run(args.server, args.out, args.prompts)
    else:
        analyse(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
