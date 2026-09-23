"""X-07 and X-08: who steers the film while the viewer is active.

One headless live run of the real Director on a story, with the same
scripted viewer lines in every arm, recorded so a judge can classify every
beat afterwards (`judge_authority.py`). The arms are evaluation-only
patches; production code is untouched.

    base              production behaviour (X-07 "momentum on", X-08 "full")
    momentum_off      X-07: the viewer-momentum block never reaches the Director
    memory_only       X-08 E2: the Director sees no arc and no scene card; the
                      Showrunner still compacts, notes and prunes
    hide_candidates   X-08 E5: the card's candidate beats are hidden from the
                      Director while a viewer direction (momentum) is active

    python scripts/experiments/story_authority.py --arm base --out logs/experiments/story-authority

Every Director call is written to `<out>/<arm>/plans.jsonl`: the segment,
the line it answers, the card in force (goal, candidate beats, whether the
Director saw them), the viewer momentum, the pacing note, what the Director
returned and how long it took. The run's own transcript, world model and
timing log sit next to it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

from src.agents import director as director_agent  # noqa: E402
from src.eval.runner import run_live  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.playback.player import Playable, Player  # noqa: E402
from src.story import context  # noqa: E402

ARMS = ("base", "momentum_off", "memory_only", "hide_candidates")

# The same lines in every arm of a schedule, sent when the given segment
# starts playing (as scripts/steer.py does). "directions": three directions,
# each followed by a stretch long enough to see whether the film holds it or
# returns to the card (X-07). "silence": two directions, then about two and
# a half minutes of silence, to see whether the film keeps moving on its
# own (X-08).
SCHEDULES = {
    "directions": {
        5: "A small paper boat drifts in; he decides to become its captain.",
        15: "Everything he touches starts to bounce like rubber.",
        27: "He gets sleepy and tries to build himself a bed.",
    },
    "silence": {
        5: "A small paper boat drifts in; he decides to become its captain.",
        13: "Everything he touches starts to bounce like rubber.",
    },
}


class HeadlessViewer(Player):
    """Holds each clip for its length over `time_scale` and types each
    scripted line when its segment starts playing."""

    def __init__(self, lines: dict[int, str], *, time_scale: float = 1.0) -> None:
        super().__init__(time_scale=time_scale)
        self.pending = sorted(lines.items())
        self.sent: list[dict] = []

    async def play(self, item: Playable) -> None:
        while self.pending and item.turn >= self.pending[0][0]:
            segment, text = self.pending.pop(0)
            self.push_input(text)
            self.sent.append({"segment": segment, "on_air": item.turn, "text": text, "wall": time.time()})
        await self._hold(item.duration)


@contextlib.contextmanager
def arm_patches(arm: str):
    """The arm's evaluation-only changes to what the Director reads."""
    format_plan = context.format_plan
    format_interventions = context.format_interventions

    def no_momentum(world, current_input):
        saved = world.viewer_momentum
        world.viewer_momentum = ""
        try:
            return format_interventions(world, current_input)
        finally:
            world.viewer_momentum = saved

    def no_plan(world, config):
        return ("Arc: (none: this film has no written plan)\n"
                "Scene card: (none: no plan is written for this film; you decide what happens next)")

    def candidates_hidden_while_directed(world, config):
        card = world.plan.scene
        if card is None or not world.viewer_momentum:
            return format_plan(world, config)
        saved = card.candidate_beats
        card.candidate_beats = []
        try:
            return format_plan(world, config) + "\n  (candidate beats hidden while the viewer's direction is active)"
        finally:
            card.candidate_beats = saved

    patches = []
    if arm == "momentum_off":
        patches.append(mock.patch.object(director_agent, "format_interventions", no_momentum))
    elif arm == "memory_only":
        patches.append(mock.patch.object(director_agent, "format_plan", no_plan))
    elif arm == "hide_candidates":
        patches.append(mock.patch.object(director_agent, "format_plan", candidates_hidden_while_directed))
    elif arm != "base":
        raise ValueError(f"unknown arm {arm!r}")
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


def recorder(path: Path, arm: str):
    """Wrap the Director call to write one line per call."""
    run = director_agent.run

    async def recorded(*, world, state, **kwargs):
        card = world.plan.scene
        seen_candidates = bool(card and card.candidate_beats) and not (
            arm == "memory_only" or (arm == "hide_candidates" and world.viewer_momentum))
        row = {
            "arm": arm, "segment": state.turn_number, "answers": (state.user_input or "").strip(),
            "card": ({"id": card.id, "goal": card.goal, "candidates": list(card.candidate_beats),
                      "exit": card.exit_condition, "seen_by_director": arm != "memory_only",
                      "candidates_seen": seen_candidates} if card else None),
            "arc": list(world.plan.arc),
            "momentum": ({"text": world.viewer_momentum, "segment": world.viewer_momentum_segment}
                         if world.viewer_momentum else None),
            "pacing": kwargs.get("pacing_note", ""),
            "story_seconds": world.seconds_since(1),
        }
        t0 = time.monotonic()
        beat, notes, retried = await run(world=world, state=state, **kwargs)
        row["director_s"] = round(time.monotonic() - t0, 3)
        row["retried"] = retried
        row["beat"] = beat.model_dump() if beat is not None else None
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return beat, notes, retried

    return mock.patch.object(director_agent, "run", recorded)


async def main_async(args) -> int:
    name = args.arm if args.schedule == "directions" and not args.rep else f"{args.arm}-{args.schedule}-{args.rep}"
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)
    config = Config.from_yaml(REPO / args.config).model_copy(update={
        "name": f"x07x08-{args.arm}", "director_service_tier": args.tier, "video_enabled": False,
    })
    story = Story.from_json(REPO / args.story)
    viewer = HeadlessViewer(SCHEDULES[args.schedule], time_scale=args.time_scale)
    (out / "plans.jsonl").unlink(missing_ok=True)
    started = time.time()
    with arm_patches(args.arm), recorder(out / "plans.jsonl", args.arm):
        await run_live(config=config, story=story, log_dir=out, player=viewer, max_segments=args.segments)
    (out / "run.json").write_text(json.dumps({
        "arm": args.arm, "schedule": args.schedule, "rep": args.rep,
        "segments": args.segments, "story": args.story, "config": args.config,
        "director_model": config.director_model or config.model, "tier": args.tier,
        "time_scale": args.time_scale, "lines": viewer.sent, "wall_s": round(time.time() - started, 1),
    }, indent=2), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--schedule", choices=sorted(SCHEDULES), default="directions")
    ap.add_argument("--rep", type=int, default=0, help="repetition number (0 = the first run, named by the arm alone)")
    ap.add_argument("--out", default="logs/experiments/story-authority")
    ap.add_argument("--segments", type=int, default=40)
    ap.add_argument("--config", default="configs/director.yaml")
    ap.add_argument("--story", default="stories/lego-void")
    ap.add_argument("--tier", default="priority", help="the Director's OpenAI service tier (the live config's)")
    ap.add_argument("--time-scale", type=float, default=1.0, help="1.0 = real time, as a viewer would see it")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
