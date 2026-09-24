"""X-19 (decision record §4.3, review 03 R12; gates ARCH-12): first-clip
streaming. How soon could the first clip of an answer beat be emitted if
the Director's reply were parsed as it streams, and do the streamed
replies stay valid?

    python scripts/experiments/x19_streaming.py run --out logs/experiments/x19 [--calls 50]
    python scripts/experiments/x19_streaming.py report --out logs/experiments/x19

One world from the live config's Director (the bootstrap and five quiet
segments of the Lego story, as `director_models.py` builds it); then
`--calls` answer calls, each from a copy of that world with one viewer
line (ten lines in rotation), in two arms interleaved per call so they
share the network's moment:

  whole     today: the production Director's call (`director.run`, the live
            config's model, tier and effort), the engine waiting for the
            whole beat. Its API time is the critical path of an answer.
  streamed  the same messages, streamed, against an evaluation-only copy of
            `BeatPlan` with `clips` after the fields the first clip needs
            (`voiceover`, `thread_ops`, the disposition, `delivered_request`,
            `perspective`); `scene_done` and `needs_replan` after the clips.
            Times: first token, the first clip's object closed, the whole
            reply. The first clip is parsed alone as a `ClipSpec` the moment
            it closes; the whole reply as a `BeatPlan`, then the validator's
            `check` against the world.

Criterion (the record): time to clip 1, schema validity over 50 answer calls.
Review 03 expected the answer path to fall from ~3.5-4 s to ~1.8 s.
Nothing in production changes: ARCH-12's arm (the schema order, a
streaming backend, the engine emitting clip 1 early) waits for the owner.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

from openai import AsyncOpenAI  # noqa: E402
from openai.lib._pydantic import to_strict_json_schema  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from scripts.experiments.director_models import CONFIG, _world  # noqa: E402
from src.agents import director as director_agent  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.story.schemas import BeatPlan, ClipSpec, ThreadOp  # noqa: E402
from src.story.validator import check  # noqa: E402

LINES = [
    "A small paper boat drifts in; he decides to become its captain.",
    "Everything he touches starts to bounce like rubber.",
    "He gets sleepy and tries to build himself a bed.",
    "He finds a tiny red umbrella and opens it.",
    "Make him dance.",
    "A red cap falls from above and lands on his head.",
    "He tries to juggle three small bricks.",
    "The floor tilts gently and everything starts to slide.",
    "A rubber duck squeaks in from the side.",
    "He builds a tiny tower of bricks and knocks it over.",
]


class StreamedBeatPlan(BaseModel):
    """`BeatPlan`'s fields, `clips` after everything the first clip needs
    (evaluation only)."""

    intent: str
    framing: str
    perspective: str = ""
    voiceover: str = ""
    thread_ops: list[ThreadOp] = Field(default_factory=list)
    intervention: Literal["none", "absorbed", "deferred", "declined"] = "none"
    intervention_reason: str = ""
    delivered_request: int = 0
    clips: list[ClipSpec]
    scene_done: bool = False
    needs_replan: bool = False


assert set(StreamedBeatPlan.model_fields) == set(BeatPlan.model_fields), "the copy has exactly BeatPlan's fields"


class FirstClip:
    """An incremental scanner over the streamed JSON text: finds the top-level
    `clips` array and reports the first element's text once it closes."""

    def __init__(self) -> None:
        self.buf: list[str] = []
        self.depth = 0
        self.in_string = self.escape = False
        self.key: list[str] | None = None      # the string being read at depth 1 (a key, or a value)
        self.last_key = ""
        self.expect_key = False
        self.in_clips = False
        self.start: int | None = None
        self.text: str | None = None
        self.pos = 0

    def feed(self, chunk: str) -> str | None:
        """Take the next piece of the reply; the first clip's text if it
        closed in this piece."""
        found = None
        for ch in chunk:
            self.buf.append(ch)
            i = self.pos
            self.pos += 1
            if self.text is not None:
                continue
            if self.in_string:
                if self.escape:
                    self.escape = False
                elif ch == "\\":
                    self.escape = True
                elif ch == '"':
                    self.in_string = False
                    if self.key is not None and self.depth == 1 and self.expect_key:
                        self.last_key = "".join(self.key)
                        self.expect_key = False
                    self.key = None
                elif self.key is not None:
                    self.key.append(ch)
                continue
            if ch == '"':
                self.in_string = True
                self.key = [] if self.depth == 1 else None
            elif ch in "{[":
                if self.depth == 1 and ch == "[" and self.last_key == "clips":
                    self.in_clips = True
                elif self.in_clips and self.depth == 2 and ch == "{" and self.start is None:
                    self.start = i
                self.depth += 1
                if self.depth == 1:
                    self.expect_key = True
            elif ch in "}]":
                self.depth -= 1
                if self.in_clips and self.start is not None and self.depth == 2 and ch == "}":
                    self.text = found = "".join(self.buf[self.start:i + 1])
            elif ch == "," and self.depth == 1:
                self.expect_key = True
        return found

    @property
    def full(self) -> str:
        return "".join(self.buf)


def _captured(backend):
    """The production backend, keeping the messages and the API time of its
    first structured call."""
    class Capture:
        def __init__(self) -> None:
            self.model, self.calls = backend.model, []

        async def generate_structured(self, messages, schema, *args, **kwargs):
            t0 = time.monotonic()
            text, usage = await backend.generate_structured(messages, schema, *args, **kwargs)
            self.calls.append({"messages": messages, "api_s": time.monotonic() - t0, "usage": usage})
            return text, usage

        async def generate(self, *args, **kwargs):
            return await backend.generate(*args, **kwargs)
    return Capture()


async def _streamed(client: AsyncOpenAI, config: Config, messages: list[dict]) -> dict:
    kw = {"model": config.director_model or config.model, "messages": messages, "stream": True,
          "stream_options": {"include_usage": True},
          "response_format": {"type": "json_schema", "json_schema": {
              "name": "BeatPlan", "strict": True, "schema": to_strict_json_schema(StreamedBeatPlan)}}}
    if config.director_service_tier:
        kw["service_tier"] = config.director_service_tier
    if config.director_reasoning_effort:
        kw.update(reasoning_effort=config.director_reasoning_effort, max_completion_tokens=config.max_tokens_director)
    else:
        kw.update(temperature=config.temperature, max_tokens=config.max_tokens_director)
    scan, first_token, clip1_s, clip1_ok, usage = FirstClip(), None, None, None, None
    t0 = time.monotonic()
    stream = await client.chat.completions.create(**kw)
    async for event in stream:
        if event.usage is not None:
            usage = event.usage.completion_tokens
        if not event.choices:
            continue
        delta = event.choices[0].delta.content or ""
        if delta and first_token is None:
            first_token = time.monotonic() - t0
        if scan.feed(delta) is not None and clip1_s is None:
            clip1_s = time.monotonic() - t0
            try:
                ClipSpec(**json.loads(scan.text))
                clip1_ok = True
            except Exception:
                clip1_ok = False
    total = time.monotonic() - t0
    return {"first_token_s": first_token, "clip1_s": clip1_s, "clip1_valid": clip1_ok, "total_s": total,
            "out_tokens": usage, "text": scan.full, "clip1_text": scan.text}


async def run(out: Path, calls: int) -> None:
    config = Config.from_yaml(CONFIG).model_copy(update={"name": "x19", "video_enabled": False})
    engine, state0 = await _world(config)
    out.mkdir(parents=True, exist_ok=True)
    (out / "world.json").write_text(engine.world.model_dump_json(indent=1))
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    rows_path = out / "calls.jsonl"
    rows_path.unlink(missing_ok=True)
    for n in range(calls):
        line = LINES[n % len(LINES)]
        world, state = copy.deepcopy(engine.world), copy.deepcopy(state0)
        state.turn_number, state.user_input = state0.turn_number + 1, line
        world.add_intervention(state.turn_number, line)
        capture = _captured(engine.llm_director)
        t0 = time.monotonic()
        beat, notes, retried = await director_agent.run(
            world=world, state=state, llm=capture, config=config, voiceover_open=False, pacing_note="",
            scene_note="", beat_status=engine._beat_status(), clip_seconds=6.0)
        whole = {"api_s": capture.calls[0]["api_s"], "turn_s": time.monotonic() - t0, "retried": retried,
                 "valid": beat is not None, "clips": len(beat.clips) if beat else None,
                 "intervention": beat.intervention if beat else None,
                 "out_tokens": (capture.calls[0]["usage"] or {}).get("completion")}
        streamed = await _streamed(client, config, capture.calls[0]["messages"])
        try:
            plan = BeatPlan(**StreamedBeatPlan(**json.loads(streamed["text"])).model_dump())
            verdict = check(plan, copy.deepcopy(world), max_clips=director_agent.clip_cap(config, line))
            streamed.update(valid=True, violations=verdict.violations, clips=len(plan.clips),
                            intervention=plan.intervention)
        except Exception as exc:
            streamed.update(valid=False, error=str(exc)[:300])
        row = {"n": n + 1, "line": line, "whole": whole, "streamed": {k: v for k, v in streamed.items() if k != "text"},
               "streamed_text": streamed["text"]}
        with rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{n + 1:3} whole {whole['api_s']:5.2f} s ({whole['clips']} clips) | streamed clip 1 at "
              f"{streamed['clip1_s'] or float('nan'):5.2f} s, whole {streamed['total_s']:5.2f} s, "
              f"valid {streamed.get('valid')}, clip 1 valid {streamed['clip1_valid']}", flush=True)
    report(out)


def _q(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))] if xs else float("nan")


def report(out: Path) -> dict:
    rows = [json.loads(line) for line in (out / "calls.jsonl").read_text().splitlines() if line.strip()]
    w = [r["whole"]["api_s"] for r in rows if r["whole"]["valid"]]
    c1 = [r["streamed"]["clip1_s"] for r in rows if r["streamed"].get("clip1_s") is not None]
    st = [r["streamed"]["total_s"] for r in rows]
    ft = [r["streamed"]["first_token_s"] for r in rows if r["streamed"].get("first_token_s") is not None]
    saved = [r["whole"]["api_s"] - r["streamed"]["clip1_s"] for r in rows if r["streamed"].get("clip1_s") is not None]
    summary = {
        "calls": len(rows),
        "whole_api_s": {"mean": statistics.mean(w), "p50": _q(w, .5), "p95": _q(w, .95), "max": max(w)},
        "streamed_clip1_s": {"mean": statistics.mean(c1), "p50": _q(c1, .5), "p95": _q(c1, .95), "max": max(c1)},
        "streamed_total_s": {"mean": statistics.mean(st), "p50": _q(st, .5), "p95": _q(st, .95), "max": max(st)},
        "streamed_first_token_s": {"mean": statistics.mean(ft), "p95": _q(ft, .95)},
        "saved_per_call_s": {"mean": statistics.mean(saved), "p50": _q(saved, .5), "min": min(saved)},
        "whole_valid": sum(r["whole"]["valid"] for r in rows), "whole_retried": sum(bool(r["whole"]["retried"]) for r in rows),
        "streamed_valid": sum(bool(r["streamed"].get("valid")) for r in rows),
        "streamed_clip1_valid": sum(bool(r["streamed"].get("clip1_valid")) for r in rows),
        "streamed_violations": sum(bool(r["streamed"].get("violations")) for r in rows),
        "clips_whole": statistics.mean(r["whole"]["clips"] for r in rows if r["whole"]["clips"]),
        "clips_streamed": statistics.mean(r["streamed"]["clips"] for r in rows if r["streamed"].get("clips")),
        "absorbed": {"whole": sum(r["whole"]["intervention"] == "absorbed" for r in rows),
                     "streamed": sum(r["streamed"].get("intervention") == "absorbed" for r in rows)},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("run", "report"))
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "x19")
    ap.add_argument("--calls", type=int, default=50)
    args = ap.parse_args(argv)
    if args.command == "run":
        asyncio.run(run(args.out, args.calls))
    else:
        report(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
