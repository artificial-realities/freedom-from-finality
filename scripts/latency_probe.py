"""Does an LTX-2.5 server render in real time? How long a clip takes to
render against how long it plays.

Renders `--clips` clips of the config's clip shape on a running server, the
first from the story's reference and the rest continuing it, and prints for
each the generation, encode and wall time, the server's stage timings and
the real-time ratio: wall seconds per aired second. Below 1.0 the player
never waits; the live loop also keeps a buffer, so a setting wants a ratio
well under it (the README's performance table has the reference setting's).
The first WARMUP renders are a fresh server's warm-up and are not counted.
Exits 1 when a warm render was slower than real time.

    python scripts/latency_probe.py --label h100-fp8 [--server http://localhost:8000] [--clips 12]

Writes logs/probe/<label>/probe.json (the server's /health, the code
revision, every render with its stage timings) with the clips alongside.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # repo root, so `src` imports

import requests  # noqa: E402

from src.i2v.ltxv import fit_reference  # noqa: E402
from src.i2v.shape import ShapeRegistry  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.util.timing_log import git_revision  # noqa: E402

RENDER_TIMEOUT_S = 900
WARMUP = 3


def prompts_for(story: Story) -> list[str]:
    """A kick from the reference, then a shrug that continues it."""
    ch = story.protagonist_character()
    name, look = ch.name, ch.look
    shows = " ".join(story.picture.reference.shows.split()).rstrip(".") if story.picture.reference else name
    style = " ".join(story.picture.style.split())
    kick = (f"{style} Medium shot, straight-on, eye level, static camera. On screen: {name} ({look}). "
            f"The shot opens with {shows}, then the action begins. A black-and-white soccer ball rolls in "
            f"from the left and stops at {name}'s feet. {name} kicks the ball; it rolls out of frame to the right. "
            f"{name} watches it go, then stands with arms at the sides, facing the camera.")
    shrug = (f"{style} Medium shot, straight-on, eye level, static camera. On screen: {name} ({look}). {name} takes "
             "two careful steps to the right, stops, and raises both hands in a small shrug, facing the camera.")
    return [kick, shrug]


def parse_timings(text: str) -> dict[str, float]:
    """The server's `X-Timings` line (`key=seconds;...`) as numbers."""
    out: dict[str, float] = {}
    for part in (text or "").split(";"):
        key, sep, value = part.partition("=")
        if sep:
            try:
                out[key.strip()] = float(value)
            except ValueError:
                pass
    return out


def render(server: str, payload: dict) -> tuple[dict | None, bytes, str]:
    """One /generate call: (row, mp4 bytes, error)."""
    t0 = time.monotonic()
    resp = requests.post(f"{server}/generate", json=payload, timeout=RENDER_TIMEOUT_S)
    wall = time.monotonic() - t0
    if resp.status_code != 200:
        return None, b"", f"HTTP {resp.status_code} {resp.text[:200]}"
    h = resp.headers
    row = {
        "frames": int(h.get("X-Num-Frames", 0) or 0),
        "generate_s": float(h.get("X-Latency-S", 0) or 0), "encode_s": float(h.get("X-Encode-S", 0) or 0),
        "wall_s": round(wall, 3), "timings": h.get("X-Timings", ""), "stages": parse_timings(h.get("X-Timings", "")),
    }
    return row, resp.content, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--label", required=True, help="name of this setting, e.g. h100-fp8")
    ap.add_argument("--clips", type=int, default=12, help=f"renders in all, the first {WARMUP} of them warm-up")
    ap.add_argument("--config", type=Path, default=REPO / "configs" / "director.live.yaml",
                    help="the clip shape, continuity window, negative prompt and reference fit")
    ap.add_argument("--story", default="stories/lego-void")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--out", default="logs/probe")
    args = ap.parse_args(argv)

    config = Config.from_yaml(args.config)
    story = Story.from_json(args.story)
    if not story.reference_image:
        print("the story has no reference image", file=sys.stderr)
        return 2

    health = requests.get(f"{args.server}/health", timeout=10).json()
    if health.get("status") != "ready":
        print(f"server is {health.get('status')}; wait for ready", file=sys.stderr)
        return 1
    width, height, fps = int(health["width"]), int(health["height"]), float(health["frame_rate"])
    shape = ShapeRegistry.from_config(config, fps=fps).default
    print(f"{health.get('gpu')} · {width}x{height} @{fps:g} · {health.get('variant')} · attention={health.get('attention')} "
          f"quant={health.get('quant')} compiled={health.get('compiled')} encoder={health.get('encoder')} · "
          f"{shape.render_frames} frames, {shape.delivered_seconds:.2f} s aired")

    out = Path(args.out) / args.label
    out.mkdir(parents=True, exist_ok=True)
    ref_png, _ = fit_reference(story.reference_image, width, height,
                               fit=config.i2v_reference_fit, fill=config.i2v_reference_fill)
    ref_b64 = base64.b64encode(ref_png).decode()
    requests.post(f"{args.server}/reset", timeout=10)
    prompts = prompts_for(story)

    rows: list[dict] = []
    for i in range(args.clips):
        first = i == 0
        payload = {"prompt": prompts[i % 2], "negative_prompt": config.ltxv_negative_prompt or None,
                   "num_frames": shape.render_frames, "continuity_strength": config.ltxv_continuity_strength,
                   "continuity_frames": config.ltxv_continuity_frames, "seed": args.seed + i,
                   "reset_window": first, "reference_image": ref_b64 if first else None}
        row, body, error = render(args.server, payload)
        if row is None:
            print(f"#{i}: {error}", file=sys.stderr)
            return 1
        row.update({"index": i, "warm": i >= WARMUP, "ratio": round(row["wall_s"] / shape.delivered_seconds, 4)})
        rows.append(row)
        (out / f"{i:02d}.mp4").write_bytes(body)
        print(f"#{i:2d}{' (warm-up)' if i < WARMUP else '          '} generate {row['generate_s']:.2f} s + encode "
              f"{row['encode_s']:.2f} s = wall {row['wall_s']:.2f} s → {row['ratio']:.3f}  [{row['timings']}]", flush=True)

    warm = sorted(r["ratio"] for r in rows if r["warm"])
    revision, dirty = git_revision()
    summary = {"warm": len(warm), "p50": warm[len(warm) // 2] if warm else None,
               "p95": warm[min(len(warm) - 1, int(0.95 * len(warm)))] if warm else None, "max": warm[-1] if warm else None}
    (out / "probe.json").write_text(json.dumps(
        {"label": args.label, "health": health, "revision": revision, "dirty": dirty, "config": str(args.config),
         "shape": shape.record(), "rows": rows, "ratio": summary}, indent=2))
    if not warm:
        print(f"\nno warm render: run more than {WARMUP} clips", file=sys.stderr)
        return 1
    real_time = summary["max"] < 1.0
    print(f"\n{len(warm)} warm renders: ratio p50 {summary['p50']:.3f}, p95 {summary['p95']:.3f}, max {summary['max']:.3f} "
          f"→ {'real time' if real_time else 'slower than real time: the film would hold'}")
    print(f"saved {out}/probe.json")
    return 0 if real_time else 1


if __name__ == "__main__":
    sys.exit(main())
