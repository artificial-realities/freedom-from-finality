"""Latency probe for the LTX-2.5 server: how long a clip takes against how
long it plays.

Renders a fixed kick-and-shrug sequence at the given clip lengths (a cut from
the reference, then continues) and prints per clip the generation, encode and
wall time, the server's per-stage timings, and the real-time ratio (wall
seconds per clip second; below 1.0 the player never waits). Results go to
<out>/<label>/probe.json with the server's /health, clips alongside, so
settings can be compared across sessions (docs/reviews/2026-09-16-real-time-plan.md).

    python scripts/latency_probe.py --label sxm-bf16 [--server http://localhost:8000] [--seconds 5,6,8] [--repeat 2]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, so `src` imports

import requests  # noqa: E402
import yaml  # noqa: E402

from src.i2v.ltxv import letterbox_reference, snap_to_frame_grid  # noqa: E402
from src.models.story import Story  # noqa: E402

STORY = Story.from_json("data/story.json")
LOOK = STORY.characters[0].look
POSE = STORY.characters[0].reference_pose.strip().rstrip(".")
STYLE = " ".join(STORY.visual_style.split())
NEGATIVE = yaml.safe_load(Path("configs/director.live.yaml").read_text()).get("ltxv_negative_prompt", "")

KICK = (f"{STYLE} Medium shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). "
        f"The shot opens with Lego Man {POSE}, then the action begins. A black-and-white LEGO soccer ball rolls in "
        "from the left and stops at his feet. Lego Man kicks the ball; it rolls out of frame to the right. He watches "
        "it go, then returns to standing with his arms at his sides. Final frame: Lego Man standing upright, facing "
        "the camera, arms at his sides.")
SHRUG = (f"{STYLE} Medium shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). Lego Man takes "
         "two careful steps to the right, stops, and raises both claw hands in a small shrug. Final frame: Lego Man "
         "standing, hands raised in a shrug, facing the camera.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--label", required=True, help="name of this setting, e.g. sxm-bf16 or b200-fp8-768")
    ap.add_argument("--seconds", default="5,6,8", help="clip lengths to probe")
    ap.add_argument("--repeat", type=int, default=2, help="clips per length (the first is a cut, the rest continue)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--reference", default="data/legoman.png")
    ap.add_argument("--out", default="logs/probe")
    args = ap.parse_args()

    health = requests.get(f"{args.server}/health", timeout=10).json()
    if health.get("status") != "ready":
        print(f"server is {health.get('status')}; wait for ready", file=sys.stderr)
        return 1
    width, height, fps = int(health["width"]), int(health["height"]), int(float(health["frame_rate"]))
    print(f"{health.get('gpu')} · {width}x{height} @{fps} · {health['variant']} {health['steps']} steps · "
          f"attention={health.get('attention')} quant={health.get('quant')} compiled={health.get('compiled')} "
          f"encoder={health.get('encoder')}")

    out = Path(args.out) / args.label
    out.mkdir(parents=True, exist_ok=True)
    ref_b64 = base64.b64encode(letterbox_reference(args.reference, width, height, fill="auto")).decode()
    requests.post(f"{args.server}/reset", timeout=10)

    rows: list[dict] = []
    for seconds in [int(s) for s in args.seconds.split(",") if s.strip()]:
        for i in range(args.repeat):
            cut = i == 0
            payload = {
                "prompt": KICK if i % 2 == 0 else SHRUG,
                "negative_prompt": NEGATIVE or None,
                "num_frames": snap_to_frame_grid(seconds * fps),
                "continuity_strength": 1.0,
                "continuity_frames": 9,
                "seed": args.seed + i,
                "reset_window": cut,
                "reference_image": ref_b64 if cut else None,
            }
            t0 = time.monotonic()
            resp = requests.post(f"{args.server}/generate", json=payload, timeout=900)
            wall = time.monotonic() - t0
            if resp.status_code != 200:
                print(f"{seconds}s #{i}: HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr)
                continue
            h = resp.headers
            clip = float(h.get("X-Duration-S", seconds))
            row = {
                "seconds": seconds, "index": i, "cut": cut, "frames": int(h.get("X-Num-Frames", 0)),
                "clip_s": clip, "generate_s": float(h.get("X-Latency-S", 0)), "encode_s": float(h.get("X-Encode-S", 0)),
                "wall_s": round(wall, 2), "ratio": round(wall / clip, 2), "timings": h.get("X-Timings", ""),
            }
            rows.append(row)
            path = out / f"{seconds}s_{i}_{'cut' if cut else 'cont'}.mp4"
            path.write_bytes(resp.content)
            print(f"{seconds}s #{i} {'cut ' if cut else 'cont'}: generate {row['generate_s']:.1f}s + encode "
                  f"{row['encode_s']:.1f}s = wall {wall:.1f}s for a {clip:.1f}s clip → {row['ratio']:.2f}x real time"
                  f"  [{row['timings']}]", flush=True)

    (out / "probe.json").write_text(json.dumps({"label": args.label, "health": health, "rows": rows}, indent=2))
    if rows:
        by_len = {}
        for r in rows:
            by_len.setdefault(r["seconds"], []).append(r["ratio"])
        print("\nreal-time ratio by clip length (mean over clips): " +
              ", ".join(f"{k}s: {sum(v) / len(v):.2f}x" for k, v in sorted(by_len.items())))
    print(f"saved {out}/probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
