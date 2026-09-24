"""GPU session 3's inputs (D-09): X-22, X-23 and X-31 as arm chains for
`gpu_session1.py run --only x22,x23,x31` (every chain starts from the
story's reference; the arms of an experiment share a seed key).

    python scripts/experiments/session3_prepare.py --out logs/experiments/gpu3

X-22 (LTX-04's renderer rules; post-implementation): ten "wild" viewer
lines, each answered by the real Director twice from the same world (after
the bootstrap and two quiet segments), with the production system prompt
and with its "Writing for the video model" section removed; each answer
beat's clips (at most two) compiled by the production compiler.
Criterion: fewer artefacts by eye; requests still honoured.

X-23 (the "makes no vocal sound" clause; post-implementation, review 04 E9):
session A's segments 1-20 through today's compiler, and the same prompts
with "<protagonist> makes no vocal sound." added to every clip without a
line of dialogue or narration. Criterion: adopt the clause only if it
changes anything (speech and tonal content where none is asked).

X-31 (native multi-shot; gate LTX-15): three scenes, each as one continuous
take and as a paragraph that names its cuts in prose (LTX-2.5's documented
convention: "the view cuts to …"), two seeds, each followed by a continuing
clip. Criterion here (no viewer preference, D-09/D-10): cuts occur when
written and not otherwise; the clip after a cut continues its last shot.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
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
from src.story.compiler import compile_clip  # noqa: E402
from src.story.validator import apply_clip  # noqa: E402

STORY = REPO / "stories" / "lego-void"
PROMPTS = REPO / "logs" / "experiments" / "gpu1" / "probe-prompts.json"
SHAPE = {"frames": 153, "window": 9}

WILD = [
    "He does a backflip off his own head.",
    "He spins on one finger like a breakdancer.",
    "He does a hundred push-ups in a row.",
    "He juggles five bricks while balancing on one leg.",
    "He melts into a puddle and reforms.",
    "He pops his head off, tosses it up and catches it.",
    "He runs in circles so fast he becomes a blur.",
    "He walks up an invisible wall and across the ceiling.",
    "He folds himself flat and slides under a door that isn't there.",
    "He sneezes so hard he flies backwards across the void.",
]

X31_SCENES = {
    "ball": (
        "A clean product photograph of a single LEGO minifigure on a seamless white cyclorama under even, shadowless "
        "studio light. A medium shot frames Lego Man, the plain LEGO minifigure with a bare yellow head, plain red "
        "torso and plain blue legs, standing straight-on; the camera remains static. A small red LEGO ball rolls in "
        "from the right and stops by his feet, and he looks down at it.",
        " The view cuts to a close-up of the red ball at his feet, its studs catching the light. A hard cut returns to "
        "the medium shot as Lego Man bends and picks the ball up.",
        " He bends and picks the ball up, holding it at chest height.",
        "Lego Man holds the red ball at chest height and turns it slowly in his claw hands; the camera remains static.",
    ),
    "wave": (
        "A clean product photograph of a single LEGO minifigure on a seamless white cyclorama under even, shadowless "
        "studio light. A wide shot shows Lego Man, the plain LEGO minifigure with a bare yellow head, plain red torso "
        "and plain blue legs, small in the frame; the camera remains static. He raises one arm and waves.",
        " The view cuts to a medium close-up of his face and raised hand as he keeps waving, then lowers it.",
        " He keeps waving, then lowers his arm.",
        "Lego Man lowers his arm and tilts his head to one side; the camera remains static.",
    ),
    "walk": (
        "A clean product photograph of a single LEGO minifigure on a seamless white cyclorama under even, shadowless "
        "studio light. A medium shot frames Lego Man, the plain LEGO minifigure with a bare yellow head, plain red "
        "torso and plain blue legs, from the side; the camera remains static. He takes three steps to the left.",
        " The view cuts to a low angle behind him as he takes two more steps and stops.",
        " He takes two more steps and stops.",
        "Lego Man stands still and looks back over his shoulder; the camera remains static.",
    ),
}


def _chain(prompts: list[str], key: str, first_segment: int = 1) -> dict:
    return {**SHAPE, "key": key,
            "chain": [{"segment": first_segment + i, "prompt": p, "words": len(p.split())} for i, p in enumerate(prompts)]}


def _write(out: Path, exp: str, arm: str, spec: dict) -> None:
    folder = out / exp / arm
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "arm.json").write_text(json.dumps(spec, indent=1))


def _without_renderer_rules(template: str) -> str:
    start = template.index("# Writing for the video model")
    end = template.index("\n# ", start + 1)
    return template[:start] + template[end + 1:]


async def x22(out: Path) -> dict:
    config = Config.from_yaml(REPO / "configs" / "director.live.yaml")
    story = Story.from_json(STORY)
    llm = build_backend(config.llm_backend, config.model)
    engine = DirectorEngine(llm=llm, config=config)
    state = StoryState.initialize(story, config_name="x22")
    for seg in (1, 2, 3):
        state.turn_number = seg
        if not engine.has_next_clip():
            await engine.plan(state)
        state = await engine.emit(state)
    world0, state0 = copy.deepcopy(engine.world), copy.deepcopy(state)
    real_load = director_agent.load_prompt
    summary = {}
    for n, line in enumerate(WILD, start=1):
        for arm in ("on", "off"):
            world, st = copy.deepcopy(world0), copy.deepcopy(state0)
            st.turn_number, st.user_input = 4, line

            def load(path, **kw):
                text = real_load(path, **kw)
                return _without_renderer_rules(text) if arm == "off" and path == director_agent.SYSTEM_PATH else text

            director_agent.load_prompt = load
            try:
                beat, notes, retried = await director_agent.run(
                    world=world, state=st, llm=engine.llm_director, config=config, voiceover_open=False,
                    pacing_note="", scene_note="", beat_status="", clip_seconds=6.0)
            finally:
                director_agent.load_prompt = real_load
            if beat is None:
                print(f"x22 line {n} {arm}: the Director failed; skipped", flush=True)
                continue
            prompts = []
            for i in range(min(2, len(beat.clips))):
                apply_clip(beat, i, world, segment_no=4 + i)
                prompts.append(compile_clip(beat, i, world, visual_style=story.visual_style,
                                            visual_anchor=story.visual_anchor, narration_mode="ltx",
                                            narrator=story.narrator, opening=(i == 0), segment_no=4 + i))
            _write(out, "x22", f"{arm}-{n:02d}", _chain(prompts, key=f"x22-{n:02d}", first_segment=4))
            summary[f"{arm}-{n:02d}"] = {"line": line, "intervention": beat.intervention, "retried": retried,
                                         "actions": [c.action for c in beat.clips[:2]]}
            print(f"x22 line {n} {arm}: {beat.intervention}; {beat.clips[0].action[:90]}", flush=True)
    (out / "x22" / "beats.json").write_text(json.dumps(summary, indent=1))
    return summary


def x23(out: Path) -> None:
    story = Story.from_json(STORY)
    hero = story.characters[0].name
    clips = [c for c in json.loads(PROMPTS.read_text())["clips"] if 1 <= c["segment"] <= 20]
    plain = [c["prompt"] for c in clips]
    clause = [p if ('says' in p or 'narrator' in p.lower()) else f"{p} {hero} makes no vocal sound." for p in plain]
    _write(out, "x23", "plain", _chain(plain, key="x23"))
    _write(out, "x23", "clause", _chain(clause, key="x23"))
    print(f"x23: {sum(a != b for a, b in zip(plain, clause))} of {len(plain)} clips carry the clause", flush=True)


def x31(out: Path) -> None:
    for scene, (base, cuts, plain_tail, follow) in X31_SCENES.items():
        for seed in (0, 1):
            _write(out, "x31", f"single-{scene}-s{seed}", _chain([base + plain_tail, follow], key=f"x31-{scene}-{seed}"))
            _write(out, "x31", f"cut-{scene}-s{seed}", _chain([base + cuts, follow], key=f"x31-{scene}-{seed}"))
    print(f"x31: {len(X31_SCENES) * 2 * 2} arms of 2 clips", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "gpu3")
    ap.add_argument("--only", default="x22,x23,x31")
    args = ap.parse_args(argv)
    only = set(args.only.split(","))
    if "x23" in only:
        x23(args.out)
    if "x31" in only:
        x31(args.out)
    if "x22" in only:
        asyncio.run(x22(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
