"""RUN-16 (D-14), the latency screen: the same Director prompts through
each candidate model, from one world, so the call times are comparable.

    python scripts/experiments/director_models.py screen --out logs/experiments/run16
    python scripts/experiments/director_models.py report --out logs/experiments/run16

`screen` builds a world with the live config's Director (the bootstrap and
five quiet segments of the Lego story), then asks every candidate for the
next beat `--reps` times without a viewer line and `--reps` times with one
(rotating over the story-authority lines), each from a copy of that world.
Per call: the wall time of the Director's whole turn (a validator re-ask
included, as on the critical path), the output tokens (reasoning tokens
included), whether it re-asked or failed, the beat's clips, the
disposition. Models run in parallel, each one's calls in sequence.

The story half of RUN-16 (does a faster Director keep the film's
authority readings?) is `story_authority.py --director-model …` and
`judge_authority.py` on the candidates this screen keeps, one folder per
model under `<out>/story/`; `story` summarises them:

    python scripts/experiments/director_models.py story --out logs/experiments/run16
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

from src.agents import director as director_agent  # noqa: E402
from src.graph.director_engine import DirectorEngine  # noqa: E402
from src.llm import build_backend  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.state.story_state import StoryState  # noqa: E402

STORY = REPO / "stories" / "lego-void"
CONFIG = REPO / "configs" / "director.live.yaml"
# (model, reasoning effort, service tier): the chat models with the live
# tier; the GPT-5 family at no reasoning (the probe of 2026-09-24: "minimal"
# is refused, "low" is slower). gpt-5.4-nano has no priority tier and ran
# at 7-9 s; gpt-5.5 at 8.6 s: left out.
CANDIDATES = [
    ("gpt-4.1", "", "priority"),
    ("gpt-4.1-mini", "", "priority"),
    ("gpt-4.1-nano", "", "priority"),
    ("gpt-4o-mini", "", "priority"),
    ("gpt-5.4-mini", "none", "priority"),
    ("gpt-5.6-luna", "none", "priority"),
    ("gpt-5.6-sol", "none", "priority"),
    ("gpt-5.6-terra", "none", "priority"),
]
LINES = [
    "A small paper boat drifts in; he decides to become its captain.",
    "Everything he touches starts to bounce like rubber.",
    "He gets sleepy and tries to build himself a bed.",
    "He finds a tiny red umbrella and opens it.",
    "Make him dance.",
]


class Metered:
    """Wraps a backend to keep the usage of each structured call."""

    def __init__(self, inner) -> None:
        self.inner, self.usage = inner, []
        self.model = inner.model

    async def generate_structured(self, messages, schema, *args, **kwargs):
        text, usage = await self.inner.generate_structured(messages, schema, *args, **kwargs)
        self.usage.append(usage)
        return text, usage

    async def generate(self, *args, **kwargs):
        return await self.inner.generate(*args, **kwargs)


async def _world(config: Config) -> tuple[DirectorEngine, StoryState]:
    engine = DirectorEngine(llm=build_backend(config.llm_backend, config.model), config=config)
    state = StoryState.initialize(Story.from_json(STORY), config_name="run16")
    for seg in range(1, 7):
        state.turn_number = seg
        if not engine.has_next_clip():
            await engine.plan(state)
        state = await engine.emit(state)
    return engine, state


async def _screen_model(model: str, effort: str, tier: str, engine: DirectorEngine, state0: StoryState,
                        config: Config, reps: int) -> list[dict]:
    backend = Metered(build_backend(config.llm_backend, model, service_tier=tier, reasoning_effort=effort))
    rows = []
    for i in range(2 * reps):
        line = LINES[(i // 2) % len(LINES)] if i % 2 else ""
        world, state = copy.deepcopy(engine.world), copy.deepcopy(state0)
        state.turn_number, state.user_input = state0.turn_number + 1, line
        if line:
            world.add_intervention(state.turn_number, line)   # as the engine records a line before its plan
        backend.usage.clear()
        t0 = time.monotonic()
        try:
            beat, notes, retried = await director_agent.run(
                world=world, state=state, llm=backend, config=config, voiceover_open=False,
                pacing_note="", scene_note="", beat_status=engine._beat_status(), clip_seconds=6.0)
        except Exception as exc:  # a refused parameter, a timeout
            beat, notes, retried = None, [f"error: {exc}"[:300]], False
        wall = time.monotonic() - t0
        rows.append({
            "model": model, "effort": effort, "tier": tier, "line": line, "wall_s": round(wall, 3),
            "calls": len(backend.usage), "retried": retried, "failed": beat is None,
            "out_tokens": sum((u or {}).get("completion", 0) or 0 for u in backend.usage),
            "clips": len(beat.clips) if beat else None, "intervention": beat.intervention if beat else None,
            "notes": notes, "beat": beat.model_dump() if beat else None,
        })
        print(f"{model:14} {'line' if line else 'quiet':5} {wall:5.2f} s  tokens {rows[-1]['out_tokens']:4}  "
              f"{'RETRY ' if retried else ''}{'FAILED ' if beat is None else ''}"
              f"{(beat.intent if beat else '')[:60]}", flush=True)
    return rows


async def screen(out: Path, reps: int, only: list[str] | None) -> None:
    config = Config.from_yaml(CONFIG).model_copy(update={"name": "run16", "video_enabled": False})
    engine, state = await _world(config)
    out.mkdir(parents=True, exist_ok=True)
    (out / "world.json").write_text(engine.world.model_dump_json(indent=1))
    picked = [c for c in CANDIDATES if not only or c[0] in only]
    results = await asyncio.gather(*[_screen_model(m, e, t, engine, state, config, reps) for m, e, t in picked])
    rows = [r for rs in results for r in rs]
    with (out / "screen.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report(out)


def _q(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


def report(out: Path) -> dict:
    rows = [json.loads(line) for line in (out / "screen.jsonl").read_text().splitlines() if line.strip()]
    summary = {}
    print(f"\n{'model':14} {'kind':5} {'n':>3} {'mean':>6} {'p50':>6} {'p95':>6} {'max':>6} {'tokens':>7} {'tok/s':>6} "
          f"{'re-ask':>6} {'failed':>6} {'absorbed':>8}")
    for model in dict.fromkeys(r["model"] for r in rows):
        for kind in ("quiet", "line"):
            rs = [r for r in rows if r["model"] == model and bool(r["line"]) == (kind == "line")]
            ok = [r for r in rs if not r["failed"]]
            walls = [r["wall_s"] for r in ok] or [float("nan")]
            toks = [r["out_tokens"] for r in ok] or [0]
            row = {"n": len(rs), "mean": statistics.mean(walls), "p50": _q(walls, 0.5), "p95": _q(walls, 0.95),
                   "max": max(walls), "tokens": statistics.mean(toks),
                   "tok_s": statistics.mean(r["out_tokens"] / r["wall_s"] for r in ok) if ok else 0,
                   "reasks": sum(r["retried"] for r in rs), "failed": sum(r["failed"] for r in rs),
                   "absorbed": sum(r["intervention"] == "absorbed" for r in ok) if kind == "line" else None}
            summary[f"{model}/{kind}"] = row
            print(f"{model:14} {kind:5} {row['n']:3} {row['mean']:6.2f} {row['p50']:6.2f} {row['p95']:6.2f} "
                  f"{row['max']:6.2f} {row['tokens']:7.0f} {row['tok_s']:6.0f} {row['reasks']:6} {row['failed']:6} "
                  f"{'' if row['absorbed'] is None else row['absorbed']:>8}")
    (out / "screen_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


# The Director prompt's writing rules, as words: an inner state without a
# visible cue, and (the Lego blueprint's) a facial expression. A crude
# count, the same for every model; the judge reads authority.
INNER = re.compile(r"\b(curious(ly)?|curiosity|wonder(s|ing)|determin(ed|ation)|excite(d|ment)|eager(ly)?|happily|sadly|"
                   r"proud(ly)?|confus(ed|ion)|amazed|frustrat(ed|ion)|bored(om)?|thoughtful(ly)?|intrigued|delighted|"
                   r"nervous(ly)?|realiz(es|ing)|decides|wants|feels|thinks|triumphant(ly)?|enthusias(m|tic))\b", re.I)
FACE = re.compile(r"\b(smil(es|ing)|grin(s|ning)?|frown(s|ing)?|eyebrows?|eyes (widen|narrow)|wink(s|ing)?)\b", re.I)


def story(runs: Path) -> dict:
    """Per model folder under `runs` (story_authority.py's runs of that
    model): the Director's call times on answers and on other calls, the
    validator re-asks, the writing-rule words per 100 clips, clips per
    beat; the judge's summary (judge_authority.py) where it exists."""
    out = {}
    for folder in sorted(p for p in runs.iterdir() if p.is_dir()):
        rows = [json.loads(line) for plans in folder.glob("*/plans.jsonl") for line in plans.read_text().splitlines() if line.strip()]
        if not rows:
            continue
        ok = [r for r in rows if r["beat"]]
        clips = [c for r in ok for c in r["beat"]["clips"]]
        text = [f"{c['action']} {c['end_state']}" for c in clips]
        answers = [r["director_s"] for r in rows if r["answers"]]
        others = [r["director_s"] for r in rows if not r["answers"]]
        row = {"runs": len(list(folder.glob("*/plans.jsonl"))), "calls": len(rows), "failed": len(rows) - len(ok),
               "reasks": sum(bool(r.get("retried")) for r in rows),
               "answer_mean": statistics.mean(answers) if answers else None, "answer_max": max(answers, default=None),
               "other_mean": statistics.mean(others), "other_p95": _q(others, 0.95), "other_max": max(others),
               "clips_per_beat": len(clips) / len(ok), "inner_per_100": 100 * sum(bool(INNER.search(t)) for t in text) / len(text),
               "face_per_100": 100 * sum(bool(FACE.search(t)) for t in text) / len(text)}
        summary = folder / "summary.json"
        if summary.exists():
            judged = json.loads(summary.read_text())
            per = [v for v in judged.values() if isinstance(v, dict) and "labels" in v]
            row["returned_to_card"] = sum(v.get("returned_to_card", 0) for v in per)
            held = [v["mean_direction_held_s"] for v in per if v.get("mean_direction_held_s") is not None]
            row["direction_held_s"] = statistics.mean(held) if held else None
            row["labels"] = {k: sum(v["labels"].get(k, 0) for v in per) for k in ("direction", "card", "both", "other")}
            after = [v["structural_per_min_after_last_line"] for v in per if v.get("structural_per_min_after_last_line") is not None]
            row["moves_per_min_after_last_line"] = statistics.mean(after) if after else None
        out[folder.name] = row
        print(folder.name, json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
    (runs / "story_summary.json").write_text(json.dumps(out, indent=1))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("screen", "report", "story"))
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "run16")
    ap.add_argument("--reps", type=int, default=10, help="calls per model without a line, and as many with one")
    ap.add_argument("--only", default="", help="comma-separated models")
    args = ap.parse_args(argv)
    if args.command == "screen":
        asyncio.run(screen(args.out, args.reps, [m for m in args.only.split(",") if m] or None))
    elif args.command == "story":
        story(args.out / "story")
    else:
        report(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
