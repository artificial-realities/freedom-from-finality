"""GPU session 4's inputs: RUN-17's size check and LTX-15's render check,
as arm chains for `gpu_session1.py run --only run17,ltx15`.

    python scripts/experiments/session4_prepare.py --out logs/experiments/gpu4 \\
        --reference logs/experiments/run17/legoman-medium-768x432.png

RUN-17 (D-15: one standard frame size, 512x288, exactly 16:9, on LTX's
grid): session A's segments 1-12 (today's compiler) from the reference,
at 640x352 with today's letterboxed square (production), at 512x288 with a
16:9 reference fitted strictly (resized whole: no crop, no padding), and at
512x288 with the letterboxed square (the size alone). Two seeds where the
reference changes. Criterion: identity, framing and detail by eye at
512x288 against 640x352; the render time from the warm probe at each size.
An arm names its size, and `gpu_session1.py` renders it only on a server
at that size (the 512x288 arms after the restart).

LTX-15 (D-13: a per-clip cut the Director may write): a scripted film of
four beats through the production engine and compiler on the Lego story
with cuts allowed, from the reference: a ball rolls in; a cut to a
close-up of it as his hand closes on it and a clip that stays in the
close-up; a cut back to the medium shot; a cut to a side view as he walks.
The same film with every cut removed is the control. Two seeds, at the
production size. Criterion: every written cut happens near the start of its
clip, the clip after continues the new shot, no cut where none is written,
identity held.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.graph.director_engine import DirectorEngine  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.state.story_state import StoryState  # noqa: E402
from tests.test_showrunner_clock import Stub  # noqa: E402

STORY = REPO / "stories" / "lego-void"
PROMPTS = REPO / "logs" / "experiments" / "gpu1" / "probe-prompts.json"
SHAPE = {"frames": 153, "window": 9}


def _chain(prompts: list[str], key: str, **extra) -> dict:
    return {**SHAPE, "key": key, **extra,
            "chain": [{"segment": 1 + i, "prompt": p, "words": len(p.split())} for i, p in enumerate(prompts)]}


def _write(out: Path, exp: str, arm: str, spec: dict) -> None:
    folder = out / exp / arm
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "arm.json").write_text(json.dumps(spec, indent=1))


def run17(out: Path, reference: str) -> None:
    prompts = [c["prompt"] for c in json.loads(PROMPTS.read_text())["clips"] if 1 <= c["segment"] <= 12]
    for seed in (0, 1):
        _write(out, "run17", f"a-640-square-s{seed}", _chain(prompts, f"run17-{seed}", size=[640, 352]))
        _write(out, "run17", f"b-512-16x9-s{seed}", _chain(prompts, f"run17-{seed}", size=[512, 288],
                                                           reference=reference, fit="strict"))
    _write(out, "run17", "c-512-square-s0", _chain(prompts, "run17-0", size=[512, 288]))
    print(f"run17: 5 arms of {len(prompts)} clips (the reference: {reference})", flush=True)


def _clip(action: str, end: str, **over) -> dict:
    base = {"cut_to": "", "action": action, "end_state": end, "camera_motion": "static", "ambient_sound": "",
            "on_screen": ["lego_man"], "dialogue": [], "entity_ops": []}
    base.update(over)
    return base


def _beat(intent: str, clips: list[dict], framing: str = "medium shot, straight-on, eye level") -> dict:
    # A beat opens on the shot the picture is in (the Director is told it).
    return {"intent": intent, "framing": framing, "clips": clips, "voiceover": "",
            "thread_ops": [], "intervention": "none", "intervention_reason": "", "perspective": "",
            "delivered_request": 0, "scene_done": False, "needs_replan": False}


BALL_IN = {"op": "introduce", "entity": "red ball", "kind": "prop",
           "description": "a small glossy red LEGO ball", "status": "at his feet", "location": "", "holder": "",
           "present": True, "posture": "", "activity": "", "position": "", "role": "", "speaks": None, "voice": ""}
HOLD = {**BALL_IN, "op": "update", "entity": "red_ball", "description": "", "status": "in his hand",
        "holder": "lego_man", "present": None}

FILM = [
    _beat("he stands and looks around", [
        _clip("Lego Man stands still, then turns his head slowly to the left and back.",
              "He stands facing the camera."),
    ]),
    _beat("a ball rolls in and he looks at it", [
        _clip("A small red LEGO ball rolls in from the right and stops by his feet. He tilts his head down toward it.",
              "He stands looking down at the red ball by his feet.", on_screen=["lego_man", "red ball"],
              entity_ops=[BALL_IN]),
        _clip("His claw hand reaches down into the frame and closes around the ball.",
              "His claw hand holds the red ball.", cut_to="close-up of the red ball at his feet",
              on_screen=["lego_man", "red_ball"], entity_ops=[HOLD]),
        _clip("The claw hand lifts the ball slowly up and out of the frame.",
              "The white floor, empty.", on_screen=["lego_man", "red_ball"]),
    ]),
    _beat("he holds the ball", [
        _clip("Lego Man stands holding the red ball at chest height and turns it slowly in his claw hands.",
              "He holds the ball at chest height.", cut_to="medium shot, straight-on, of Lego Man",
              on_screen=["lego_man", "red_ball"]),
    ], framing="close-up of the red ball at his feet"),
    _beat("he walks off with it", [
        _clip("He takes two slow steps forward, the ball held against his chest.",
              "He stands a little further on, the ball at his chest.",
              cut_to="side view of Lego Man from his left, full figure", on_screen=["lego_man", "red_ball"]),
        _clip("He stops and turns his head toward the camera.", "He stands in profile, head turned to the camera.",
              on_screen=["lego_man", "red_ball"]),
    ], framing="medium shot, straight-on, of Lego Man"),
]


async def _film_prompts(cuts: bool) -> list[str]:
    """The scripted film's compiled prompts, emitted by the production
    engine from a stub Director, on the Lego story with cuts allowed (the
    control: the same beats with every `cut_to` empty)."""
    beats = json.loads(json.dumps(FILM))
    if not cuts:
        for beat in beats:
            for clip in beat["clips"]:
                clip["cut_to"] = ""
    with tempfile.TemporaryDirectory() as d:
        story_dir = Path(d) / "lego"
        shutil.copytree(STORY, story_dir)
        spec = json.loads((story_dir / "story.json").read_text())
        spec.setdefault("perspective", {})["cuts"] = True
        (story_dir / "story.json").write_text(json.dumps(spec))
        config = Config(name="ltx15", graph="director", llm_backend="mock", model="mock", compaction_interval=1000,
                        showrunner_min_gap=1000, stagnation_seconds=10_000.0, scene_seconds=100_000)
        stub = Stub(beats)
        engine = DirectorEngine(llm=stub, config=config)
        engine.llm = engine.llm_director = engine.llm_showrunner = stub
        state = StoryState.initialize(Story.from_json(story_dir), config_name="ltx15")
        prompts = []
        for seg in range(1, sum(len(b["clips"]) for b in beats) + 1):
            state.turn_number = seg
            if not engine.has_next_clip():
                await engine.plan(state)
            await engine.emit(state)
            prompts.append(state.current_shot.i2v_prompt)
    return prompts


def ltx15(out: Path) -> None:
    for cuts in (True, False):
        prompts = asyncio.run(_film_prompts(cuts))
        for seed in (0, 1):
            _write(out, "ltx15", f"{'cut' if cuts else 'plain'}-s{seed}", _chain(prompts, f"ltx15-{seed}"))
        if cuts:
            (out / "ltx15" / "prompts-cut.json").write_text(json.dumps(prompts, indent=1))
            written = [i + 1 for i, p in enumerate(prompts) if "The view cuts to" in p]
            print(f"ltx15: {len(prompts)} clips, cuts written in clips {written}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "gpu4")
    ap.add_argument("--reference", default="logs/experiments/run17/legoman-medium-768x432.png",
                    help="RUN-17's 16:9 reference, a path from the repo root (shipped with the arms)")
    ap.add_argument("--only", default="run17,ltx15")
    args = ap.parse_args(argv)
    only = set(args.only.split(","))
    if "run17" in only:
        run17(args.out, args.reference)
    if "ltx15" in only:
        ltx15(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
