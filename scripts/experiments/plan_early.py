"""RUN-15 (D-14): does planning the next beat as soon as a beat's last clip
is out (`plan_early`) cut stalls without costing steering? The JIT
simulation (`tests/jit_sim.py`), both arms, with the Director's call times
resampled from real runs (a clipped normal's floor reaches zero and pulls
the running estimate down, which real calls never do).

    python scripts/experiments/plan_early.py --x10 logs/live-box/x10/sessions/<id>/timing.jsonl \\
        --fast logs/live-box/session2/sessions/<id>/timing.jsonl --out logs/batch-033/plan_early.json

Regimes: X-10's Director (the slow US-morning day) and GPU session 2's (the
fast night). Per regime, a quiet film and one with a line every six clips,
each line sent at the same moment of the film in both arms (`into_clip`,
offsets cycling over the clip) so the steering difference is paired.
Eight seeds of 60 clips per regime, film and arm.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.jit_sim import Line, Samples, simulate  # noqa: E402

OFFSETS = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 1.0, 3.0, 5.0)


def _lines() -> list[Line]:
    return [Line(f"line {i}", after_clip=k, into_clip=OFFSETS[i % len(OFFSETS)]) for i, k in enumerate(range(6, 60, 6))]


async def _one(director: Samples, early: bool, seed: int, with_lines: bool) -> dict:
    sim = await simulate(clips=60, lines=_lines() if with_lines else [], seed=seed, director=director, plan_early=early)
    received = {r["line"]: r["t"] for r in sim.of("line_received")}
    on_air = {r["turn"]: r["t"] for r in sim.of("playing")}
    landed = {line: r["turn"] for r in sim.of("line_landed") for line in r["lines"]}
    steer = {line.text: round(on_air[landed[line.id]] - received[line.id], 2)
             for line in sim.lines if line.id in landed and landed[line.id] in on_air}
    film, calls = sim.measured["film"], sim.measured["director"]
    return {"stalls": film["stalls"], "held_s": round(film.get("held_s", 0), 2),
            "min_margin": round(film["min_queue_margin_s"], 2), "steer": steer,
            "calls": calls["calls"], "cancelled": calls["cancelled"]}


async def run(regimes: dict[str, Path], seeds: int) -> dict:
    out = {}
    for regime, path in regimes.items():
        director = Samples.director_calls(path)
        xs = sorted(director.values)
        print(f"{regime}: {len(xs)} calls, mean {statistics.mean(xs):.2f} p95 {xs[int(0.95 * (len(xs) - 1))]:.2f} "
              f"max {xs[-1]:.2f}", flush=True)
        for with_lines in (False, True):
            tag = f"{regime}-{'lines' if with_lines else 'quiet'}"
            rows = {early: [await _one(director, early, s, with_lines) for s in range(seeds)] for early in (False, True)}
            out[tag] = {str(k): v for k, v in rows.items()}
            for early, rs in rows.items():
                st = [v for r in rs for v in r["steer"].values()]
                print(f"  {tag} plan_early={early}: stalls {sum(r['stalls'] for r in rs)} "
                      f"held {sum(r['held_s'] for r in rs):.1f} s, min margin {min(r['min_margin'] for r in rs)}, "
                      f"calls {sum(r['calls'] for r in rs)} (cancelled {sum(r['cancelled'] for r in rs)})"
                      + (f"; steering median {statistics.median(st):.2f} max {max(st):.2f}" if st else ""), flush=True)
            if with_lines:
                diffs = [b["steer"][k] - a["steer"][k] for a, b in zip(rows[False], rows[True]) for k in a["steer"]
                         if k in b["steer"]]
                print(f"  {tag} paired steering (early − late): n {len(diffs)} mean {statistics.mean(diffs):+.2f}, "
                      f"worse by > 0.5 s {sum(d > 0.5 for d in diffs)}, better {sum(d < -0.5 for d in diffs)}", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--x10", type=Path, required=True, help="X-10's timing.jsonl (the slow Director)")
    ap.add_argument("--fast", type=Path, required=True, help="GPU session 2's timing.jsonl (the fast Director)")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    out = asyncio.run(run({"x10": args.x10, "session2": args.fast}, args.seeds))
    if args.out:
        args.out.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
