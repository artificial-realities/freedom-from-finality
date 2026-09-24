"""Latency probe for the LTX-2.5 server: how long a clip takes against how
long it plays.

Two modes.

  --seconds 5,6,8 --repeat 2   Renders a fixed kick-and-shrug pair at each
      clip length (a cut from the reference, then continues) and prints per
      clip the generation, encode and wall time, the server's per-stage
      timings and the real-time ratio (wall seconds per clip second; below
      1.0 the player never waits). For comparing server settings
      (docs/reviews/2026-09-16-real-time-plan.md).

  --warm 30   The regression check after a server, i2v, shape or recipe
      change (RUN-13): N renders of the baseline's shape on one warmed
      server, the first from the reference and the rest continuing, with
      real prompts from `--prompts` (a `scripts/replay_compiler.py run`
      output, so the text encoder sees a film's prompt lengths) or the
      built-in pair. The first `cold_warm.warm_after_clips` renders are
      warm-up. The warm renders' client-side ratio p50, p95 and max are
      compared with `benchmarks/runtime/baseline.json` at `--tolerance`
      (+3 %), and a warm render slower than `mid_film_compiles.factor`
      times the warm median is a mid-run compile. Exit status 1 on a
      failure.

Results go to <out>/<label>/probe.json (the server's /health, the code
revision, every row with its stage timings, the comparison) with the
clips alongside, so settings can be compared across sessions.

    python scripts/latency_probe.py --label sxm-bf16 [--server http://localhost:8000] [--seconds 5,6,8] [--repeat 2]
    python scripts/latency_probe.py --label h100-fp8 --warm 30 --prompts logs/.../prompts.json
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # repo root, so `src` imports

import requests  # noqa: E402
import yaml  # noqa: E402

from src.i2v.ltxv import letterbox_reference  # noqa: E402
from src.i2v.shape import snap_to_frame_grid  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.util.timing_log import git_revision  # noqa: E402

BASELINE = REPO / "benchmarks" / "runtime" / "baseline.json"
LIVE_CONFIG = REPO / "configs" / "director.live.yaml"
RENDER_TIMEOUT_S = 900


# ── Prompts ──────────────────────────────────────────────────────────

def builtin_prompts(story: Story) -> list[str]:
    """The fixed pair: a kick from the reference, then a shrug that continues."""
    ch = story.characters[0]
    look = ch.look
    pose = ch.reference_pose.strip().rstrip(".")
    style = " ".join(story.visual_style.split())
    name = ch.name
    kick = (f"{style} Medium shot, straight-on, eye level, static camera. On screen: {name} ({look}). "
            f"The shot opens with {name} {pose}, then the action begins. A black-and-white soccer ball rolls in "
            f"from the left and stops at his feet. {name} kicks the ball; it rolls out of frame to the right. He watches "
            f"it go, then returns to standing with his arms at his sides. Final frame: {name} standing upright, facing "
            "the camera, arms at his sides.")
    shrug = (f"{style} Medium shot, straight-on, eye level, static camera. On screen: {name} ({look}). {name} takes "
             f"two careful steps to the right, stops, and raises both hands in a small shrug. Final frame: {name} "
             "standing, hands raised in a shrug, facing the camera.")
    return [kick, shrug]


def prompts_from_file(path: Path) -> list[str]:
    """The compiled prompts of a `replay_compiler.py run` output, in order."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    prompts = [c["prompt"] for c in data.get("clips", []) if c.get("prompt")]
    if not prompts:
        raise SystemExit(f"{path} holds no prompts")
    return prompts


# ── Rendering ────────────────────────────────────────────────────────

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


def render(server: str, payload: dict, *, timeout: float = RENDER_TIMEOUT_S) -> tuple[dict | None, bytes, str]:
    """One /generate call: (row, mp4 bytes, error). The row carries the
    client wall time and what the server's headers say."""
    t0 = time.monotonic()
    resp = requests.post(f"{server}/generate", json=payload, timeout=timeout)
    wall = time.monotonic() - t0
    if resp.status_code != 200:
        return None, b"", f"HTTP {resp.status_code} {resp.text[:200]}"
    h = resp.headers
    clip = float(h.get("X-Duration-S", 0) or 0)
    row = {
        "frames": int(h.get("X-Num-Frames", 0) or 0), "clip_s": clip,
        "generate_s": float(h.get("X-Latency-S", 0) or 0), "encode_s": float(h.get("X-Encode-S", 0) or 0),
        "wall_s": round(wall, 3), "ratio": round(wall / clip, 4) if clip else None,
        "timings": h.get("X-Timings", ""), "stages": parse_timings(h.get("X-Timings", "")),
    }
    return row, resp.content, ""


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ── The warm comparison ──────────────────────────────────────────────

def compare_warm(rows: list[dict], baseline: dict, *, tolerance: float = 0.03) -> dict:
    """The warm rows' ratio tails against the baseline's, each allowed
    `tolerance` above it, and the mid-run compile check."""
    warm_after = int(baseline["cold_warm"]["warm_after_clips"])
    factor = float(baseline["gates"]["mid_film_compiles"]["factor"])
    base = baseline["renders"]["ratio"]
    warm = [r for r in rows[warm_after:] if r.get("ratio") is not None]
    ratios = [r["ratio"] for r in warm]
    walls = [r["wall_s"] for r in warm]
    checks: list[dict] = []
    if warm:
        p50, p95, mx = percentile(ratios, 50), percentile(ratios, 95), max(ratios)
        for name, value, ref in (("ratio_p50", p50, base["p50"]), ("ratio_p95", p95, base["p95"]),
                                 ("ratio_max", mx, base["max"])):
            limit = ref * (1.0 + tolerance)
            checks.append({"metric": name, "value": round(value, 4), "baseline": ref, "threshold": round(limit, 4),
                           "result": "PASS" if value <= limit else "FAIL"})
        median_wall = percentile(walls, 50)
        compiles = [i + warm_after for i, r in enumerate(warm) if r["wall_s"] > factor * median_wall]
        checks.append({"metric": "mid_run_compiles", "value": len(compiles), "baseline": 0, "threshold": 0,
                       "result": "PASS" if not compiles else "FAIL", "at": compiles})
    else:
        for name in ("ratio_p50", "ratio_p95", "ratio_max", "mid_run_compiles"):
            checks.append({"metric": name, "value": None, "baseline": None, "threshold": None,
                           "result": "FAIL", "note": f"no warm render (need more than {warm_after})"})
    stage_names = sorted({k for r in warm for k in r.get("stages", {})})
    stages = {k: round(sum(r["stages"].get(k, 0.0) for r in warm) / len(warm), 3) for k in stage_names} if warm else {}
    return {
        "warm": len(warm), "warm_after": warm_after, "tolerance": tolerance, "compile_factor": factor,
        "ratio": {"n": len(ratios), "mean": round(sum(ratios) / len(ratios), 4) if ratios else None,
                  "p50": percentile(ratios, 50), "p95": percentile(ratios, 95), "max": max(ratios) if ratios else None},
        "wall_s": {"p50": percentile(walls, 50), "p95": percentile(walls, 95), "max": max(walls) if walls else None},
        "stages_mean_s": stages, "checks": checks,
    }


# ── Main ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--label", required=True, help="name of this setting, e.g. sxm-bf16 or b200-fp8-768")
    ap.add_argument("--seconds", default=None, help="clip lengths to probe (default 5,6,8; --warm uses the baseline's shape)")
    ap.add_argument("--repeat", type=int, default=2, help="clips per length (the first is a cut, the rest continue)")
    ap.add_argument("--warm", type=int, default=0, metavar="N", help="N renders of one shape, compared with the baseline")
    ap.add_argument("--prompts", type=Path, default=None, help="a replay_compiler.py run output to take prompts from")
    ap.add_argument("--frames", type=int, default=0, help="--warm: this frame count instead of the baseline's shape")
    ap.add_argument("--overlap", type=int, default=0,
                    help="--warm: frames at each clip's head that the overlap seam does not air (D-03: 9 of 153); "
                         "the ratio is then over the aired frames")
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--tolerance", type=float, default=0.03, help="allowed rise over the baseline's ratios")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--story", default="stories/lego-void")
    ap.add_argument("--reference", default=None, help="reference image (default: the story's seed image)")
    ap.add_argument("--config", type=Path, default=LIVE_CONFIG, help="the live config: continuity frames, negative prompt")
    ap.add_argument("--out", default="logs/probe")
    args = ap.parse_args(argv)

    story = Story.from_json(args.story)
    reference = args.reference or story.seed_image
    if not reference:
        print("no reference image: pass --reference", file=sys.stderr)
        return 2
    live = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {} if Path(args.config).exists() else {}
    negative = live.get("ltxv_negative_prompt", "") or ""
    continuity_frames = int(live.get("ltxv_continuity_frames", 9))

    health = requests.get(f"{args.server}/health", timeout=10).json()
    if health.get("status") != "ready":
        print(f"server is {health.get('status')}; wait for ready", file=sys.stderr)
        return 1
    width, height, fps = int(health["width"]), int(health["height"]), int(float(health["frame_rate"]))
    print(f"{health.get('gpu')} · {width}x{height} @{fps} · {health.get('variant')} {health.get('steps')} steps · "
          f"attention={health.get('attention')} quant={health.get('quant')} compiled={health.get('compiled')} "
          f"encoder={health.get('encoder')}")

    out = Path(args.out) / args.label
    out.mkdir(parents=True, exist_ok=True)
    ref_b64 = base64.b64encode(letterbox_reference(reference, width, height, fill="auto")).decode()
    requests.post(f"{args.server}/reset", timeout=10)
    revision, dirty = git_revision()
    result: dict = {"label": args.label, "health": health, "revision": revision, "dirty": dirty,
                    "story": args.story, "reference": str(reference), "seed": args.seed,
                    "continuity_frames": continuity_frames}

    def payload(prompt: str, frames: int, index: int, cut: bool) -> dict:
        return {"prompt": prompt, "negative_prompt": negative or None, "num_frames": frames,
                "continuity_strength": 1.0, "continuity_frames": continuity_frames, "seed": args.seed + index,
                "reset_window": cut, "reference_image": ref_b64 if cut else None}

    if args.warm:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        frames = args.frames or (snap_to_frame_grid(int(args.seconds.split(",")[0]) * fps) if args.seconds
                                 else int(baseline["shape"]["render_frames"]))
        if not args.overlap and frames == int(baseline["shape"]["render_frames"]):
            # The baseline's shape airs fewer frames than it renders under the overlap seam.
            args.overlap = int(baseline["shape"]["render_frames"]) - int(baseline["shape"].get("delivered_frames", frames))
        prompts = prompts_from_file(args.prompts) if args.prompts else builtin_prompts(story)
        warm_after = int(baseline["cold_warm"]["warm_after_clips"])
        rows: list[dict] = []
        for i in range(args.warm):
            cut = i == 0
            row, body, error = render(args.server, payload(prompts[i % len(prompts)], frames, i, cut))
            if row is None:
                print(f"warm #{i}: {error}", file=sys.stderr)
                result.update({"mode": "warm", "rows": rows, "error": f"render {i} failed: {error}"})
                (out / "probe.json").write_text(json.dumps(result, indent=2))
                return 1
            row.update({"index": i, "cut": cut, "warm": i >= warm_after, "prompt_words": len(prompts[i % len(prompts)].split())})
            if args.overlap and row["frames"]:
                # The overlap seam airs the new frames only (D-03): the ratio the schedule lives by.
                row["aired_s"] = round((row["frames"] - args.overlap) / fps, 4)
                row["ratio"] = round(row["wall_s"] / row["aired_s"], 4)
            rows.append(row)
            (out / f"warm_{i:02d}_{'cut' if cut else 'cont'}.mp4").write_bytes(body)
            print(f"warm #{i:2d} {'cut ' if cut else 'cont'}{' (warm-up)' if i < warm_after else ''}: generate "
                  f"{row['generate_s']:.1f}s + encode {row['encode_s']:.1f}s = wall {row['wall_s']:.2f}s for a "
                  f"{row['clip_s']:.2f}s clip → {row['ratio']:.3f}x  [{row['timings']}]", flush=True)
        comparison = compare_warm(rows, baseline, tolerance=args.tolerance)
        result.update({"mode": "warm", "frames": frames, "overlap": args.overlap, "prompts": str(args.prompts) if args.prompts else "builtin",
                       "baseline": {"path": str(args.baseline), "label": baseline.get("label")},
                       "rows": rows, "comparison": comparison})
        (out / "probe.json").write_text(json.dumps(result, indent=2))
        r = comparison["ratio"]
        print(f"\n{comparison['warm']} warm renders of {frames} frames: ratio p50 {r['p50']} p95 {r['p95']} max {r['max']}"
              f" (baseline {baseline.get('label')}, +{args.tolerance:.0%})")
        for c in comparison["checks"]:
            value = "—" if c["value"] is None else c["value"]
            print(f"  {c['result']:<5} {c['metric']:<18} {value} (≤ {c['threshold']}; baseline {c['baseline']})"
                  + (f"  at {c['at']}" if c.get("at") else "") + (f"  {c['note']}" if c.get("note") else ""))
        print(f"saved {out}/probe.json")
        return 1 if any(c["result"] == "FAIL" for c in comparison["checks"]) else 0

    kick, shrug = builtin_prompts(story)
    rows = []
    for seconds in [int(s) for s in (args.seconds or "5,6,8").split(",") if s.strip()]:
        for i in range(args.repeat):
            cut = i == 0
            row, body, error = render(args.server, payload(kick if i % 2 == 0 else shrug, snap_to_frame_grid(seconds * fps), i, cut))
            if row is None:
                print(f"{seconds}s #{i}: {error}", file=sys.stderr)
                continue
            row.update({"seconds": seconds, "index": i, "cut": cut})
            rows.append(row)
            (out / f"{seconds}s_{i}_{'cut' if cut else 'cont'}.mp4").write_bytes(body)
            print(f"{seconds}s #{i} {'cut ' if cut else 'cont'}: generate {row['generate_s']:.1f}s + encode "
                  f"{row['encode_s']:.1f}s = wall {row['wall_s']:.1f}s for a {row['clip_s']:.1f}s clip → {row['ratio']:.2f}x real time"
                  f"  [{row['timings']}]", flush=True)
    result.update({"mode": "sweep", "rows": rows})
    (out / "probe.json").write_text(json.dumps(result, indent=2))
    if rows:
        by_len: dict[int, list[float]] = {}
        for r in rows:
            by_len.setdefault(r["seconds"], []).append(r["ratio"])
        print("\nreal-time ratio by clip length (mean over clips): " +
              ", ".join(f"{k}s: {sum(v) / len(v):.2f}x" for k, v in sorted(by_len.items())))
    print(f"saved {out}/probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
