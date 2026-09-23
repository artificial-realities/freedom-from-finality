"""X-09: does deadline-aware line admission remove Director-caused stalls
without raising steering latency?

Monte Carlo over the offline JIT simulation (`tests/jit_sim.py`: the live
config at 25x, the mock Director and replayed clips, render walls and
Director calls drawn from session A's runtime baseline), with a Director
tail of 5 % re-asks at twice the time, and adversarial viewer lines: in the
middle of a Director call, 0.9 / 0.6 / 0.3 s before a render fire, just
after one, and a second line right after the first one's answer.

Arms (the record's X-09: slack 3.0 / 1.0 / 0.5 under RUN-06's candidate):

    current-4.7     production: 1x EMA render estimate + a 4.7 s buffer, lines admitted at once
    deadline-3.5    RUN-06 candidate: p95 estimates floored by the baseline, deadline admission, buffer 3.5 s (slack 3.0)
    deadline-1.5    the same, buffer 1.5 s (slack 1.0)
    deadline-1.0    the same, buffer 1.0 s (slack 0.5)

Success (the record): zero stalls; minimum queue margin >= 1 s; the
latency distribution unchanged.

    python scripts/experiments/admission.py --arm deadline-1.5 --seeds 10 --out logs/experiments/admission
    python scripts/experiments/admission.py --summary logs/experiments/admission
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.jit_sim import Line, simulate  # noqa: E402

ARMS = {
    "current-4.7": ("current", 4.7),
    "deadline-3.5": ("deadline", 3.5),
    "deadline-1.5": ("deadline", 1.5),
    "deadline-1.0": ("deadline", 1.0),
}
DIRECTOR_RETRY_RATE = 0.05


def lines() -> list[Line]:
    return [
        Line("the ground starts to tilt", after_clip=6, mid_call=0.6),
        Line("a paper plane drifts past", after_clip=9, before_fire=0.9),
        Line("he starts to hum", after_clip=12, before_fire=0.6),
        Line("the light turns warm", after_clip=15, before_fire=0.3),
        Line("a small cloud forms overhead", after_clip=18, before_fire=-0.3),
        Line("a soccer ball appears at his feet", after_clip=21, before_fire=-0.3),
        Line("make it bounce", after_clip=21, after_answer=0.4),
        Line("he sits down", after_clip=26, before_fire=0.1),
        Line("a hat falls from above", after_clip=30, mid_call=0.3),
    ]


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


async def run_arm(arm: str, seeds: int, clips: int, out: Path) -> dict:
    policy, buffer = ARMS[arm]
    rows = []
    for seed in range(seeds):
        t0 = time.monotonic()
        sim = await simulate(clips=clips, lines=lines(), seed=seed, policy=policy, buffer_seconds=buffer,
                             director_retry_rate=DIRECTOR_RETRY_RATE)
        film = sim.measured["film"]
        rows.append({
            "seed": seed, "stalls": film["stalls"], "stalled_turns": film["stalled_turns"],
            "held_s": round(film["held_s"], 3), "min_margin_s": film["min_queue_margin_s"],
            "steering_s": film["steering_s"], "lines_sent": sum(1 for x in sim.lines if x.id is not None),
            "lines_landed": film["lines_landed"], "lines_dropped": film["lines_dropped"],
            "lines_unlanded": film["lines_unlanded"],
            "deferred": len(sim.of("line_deferred")),
            "director_calls": sim.measured["director"]["calls"],
            "cancelled": sim.measured["director"]["cancelled"],
            "wall_s": round(time.monotonic() - t0, 1),
        })
        print(f"{arm} seed {seed}: stalls {rows[-1]['stalls']} min margin {rows[-1]['min_margin_s']} "
              f"deferred {rows[-1]['deferred']}", flush=True)
    result = {"arm": arm, "policy": policy, "buffer_seconds": buffer, "director_retry_rate": DIRECTOR_RETRY_RATE,
              "clips": clips, "seeds": seeds, "runs": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{arm}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def summarise(folder: Path) -> dict:
    summary = {}
    for path in sorted(folder.glob("*.json")):
        if path.name == "summary.json":
            continue
        data = json.loads(path.read_text())
        runs = data["runs"]
        steering = [s for r in runs for s in r["steering_s"]]
        margins = [r["min_margin_s"] for r in runs if r["min_margin_s"] is not None]
        summary[data["arm"]] = {
            "policy": data["policy"], "buffer_seconds": data["buffer_seconds"], "runs": len(runs),
            "stalls": sum(r["stalls"] for r in runs), "runs_with_stalls": sum(1 for r in runs if r["stalls"]),
            "held_s": round(sum(r["held_s"] for r in runs), 3),
            "min_margin_s": round(min(margins), 3) if margins else None,
            "min_margin_p05_s": pct(margins, 0.05),
            "steering_n": len(steering), "steering_p50_s": pct(steering, 0.5), "steering_p95_s": pct(steering, 0.95),
            "steering_mean_s": round(statistics.mean(steering), 3) if steering else None,
            "deferred": sum(r["deferred"] for r in runs),
            "lines_unlanded": sum(len(r["lines_unlanded"]) for r in runs),
            "lines_dropped": sum(len(r["lines_dropped"]) if isinstance(r["lines_dropped"], list) else r["lines_dropped"]
                                 for r in runs),
        }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{'arm':14} {'stalls':>6} {'runs':>5} {'held s':>7} {'min m':>6} {'p05 m':>6} {'steer p50':>9} "
          f"{'p95':>6} {'mean':>6} {'deferred':>8} {'unlanded':>8}")
    for arm, s in summary.items():
        print(f"{arm:14} {s['stalls']:6} {s['runs_with_stalls']:>2}/{s['runs']:<2} {s['held_s']:7} "
              f"{s['min_margin_s']!s:>6} {s['min_margin_p05_s']!s:>6} {s['steering_p50_s']!s:>9} "
              f"{s['steering_p95_s']!s:>6} {s['steering_mean_s']!s:>6} {s['deferred']:8} {s['lines_unlanded']:8}")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arm", choices=sorted(ARMS))
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--clips", type=int, default=40)
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "admission")
    ap.add_argument("--summary", type=Path, help="summarise the arms written to this folder")
    args = ap.parse_args(argv)
    if args.summary:
        summarise(args.summary)
        return 0
    if not args.arm:
        ap.error("--arm or --summary")
    asyncio.run(run_arm(args.arm, args.seeds, args.clips, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
