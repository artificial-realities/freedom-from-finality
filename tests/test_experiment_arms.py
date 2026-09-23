"""Experiment-only arms stay off by default and do what their experiment
needs: LTX-01's caption-prose compiler (X-05) and RUN-06's deadline
admission (X-09). Neither is production behaviour; both are selected only
by a config flag an experiment sets."""

from __future__ import annotations

from src.models.config import Config
from src.models.story import Story
from src.story import compiler_prose
from src.story.schemas import BeatPlan, ClipSpec, EntityOp
from src.story.validator import apply_clip
from src.story.world import EventRecord, WorldModel
from tests.jit_sim import Line, simulate


def test_the_arms_are_off_by_default():
    cfg = Config(name="t", llm_backend="mock", model="m")
    assert cfg.compiler == "labels" and cfg.jit_policy == "current"
    for path in ("configs/director.yaml", "configs/director.live.yaml", "configs/director.replay.yaml"):
        loaded = Config.from_yaml(path)
        assert loaded.compiler == "labels" and loaded.jit_policy == "current", path


def _world() -> WorldModel:
    return WorldModel.from_story(Story.from_json("stories/lego-void"))


def test_the_prose_compiler_writes_one_caption_paragraph():
    """LTX-01 / LTX-06: style clause, the opening state from the world, the
    framing as prose, the entrance, the action with its sound, the ending,
    silence stated, the narrator as an unseen voice; one paragraph."""
    story = Story.from_json("stories/lego-void")
    w = _world()
    hero = w.entities["lego_man"]
    hero.posture, hero.position = "sitting", "centre frame"
    w.events.append(EventRecord(segment=4, scene_id=1, narration="x", on_screen=["lego_man"]))
    beat = BeatPlan(intent="a ball", framing="medium shot, straight-on", voiceover="A visitor.", clips=[ClipSpec(
        action="A soccer ball rolls in and stops by his feet. He looks down at it.",
        end_state="he looks down at the ball", camera_motion="slowly pushes in toward him",
        ambient_sound="a soft rolling rumble", on_screen=["lego_man", "soccer ball"],
        entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a black-and-white studded ball",
                             present=True)])])
    apply_clip(beat, 0, w, segment_no=5)
    prompt = compiler_prose.compile_clip(beat, 0, w, visual_style=story.visual_style,
                                         visual_anchor=story.visual_anchor, narration_mode="ltx",
                                         narrator=story.narrator, segment_no=5)
    assert "\n" not in prompt, "one paragraph"
    order = [prompt.index(x) for x in (
        "Clean product photograph", "Lego Man, the same plain LEGO minifigure", "exactly as the shot opens",
        "A medium shot frames Lego Man straight-on as the camera slowly pushes in toward him",
        "A soccer ball, a black-and-white studded ball, rolls in and stops by his feet, with a soft rolling rumble.",
        "By the end of the shot, he looks down at the ball.",
        'An unseen narrator with a warm, unhurried documentary voice, speaking English, says, "A visitor."')]
    assert order == sorted(order), prompt
    assert "is sitting, centre frame" in prompt and "On screen:" not in prompt and "Final frame:" not in prompt
    quiet = BeatPlan(intent="still", framing="wide shot", clips=[ClipSpec(action="He waits.", end_state="he waits",
                                                                          on_screen=["lego_man"])])
    p = compiler_prose.compile_clip(quiet, 0, w, visual_style=story.visual_style, visual_anchor=story.visual_anchor,
                                    narration_mode="ltx", segment_no=6)
    assert p.endswith("The scene is silent.") and "the camera remains static" in p
    assert compiler_prose.word_note(p, opening=False).endswith("target") or compiler_prose.word_note(p, opening=False) == ""
    assert compiler_prose.word_note("word " * 250, opening=True).startswith("250 words: above the 200-word ceiling")


async def test_the_engine_uses_the_prose_arm_only_when_asked():
    import tempfile
    from src.eval.runner import run_live
    from tests.test_engine import Scripted, _config
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(compiler="prose"), story=story, log_dir=d, player=Scripted(),
                               max_segments=2)
    assert "exactly as the shot opens" in state.history[1].shot.i2v_prompt
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(), story=story, log_dir=d, player=Scripted(), max_segments=2)
    assert "exactly as the shot opens" not in state.history[1].shot.i2v_prompt
    try:
        _config(compiler="haiku")
        from src.graph.director_engine import DirectorEngine
        from src.llm import build_backend
        DirectorEngine(llm=build_backend("mock", "mock"), config=_config(compiler="haiku"))
        raise AssertionError("an unknown compiler is refused")
    except ValueError:
        pass


async def test_deadline_admission_defers_a_line_it_cannot_answer_in_time():
    """RUN-06's candidate (X-09 only): a line 0.3 s before a fire cannot be
    answered before that fire, so it is answered from the clip after it;
    the schedule's estimates are the rolling p95 floored by the baseline."""
    sim = await simulate(clips=16, lines=[Line("the light turns warm", after_clip=8, before_fire=0.3)],
                         policy="deadline", buffer_seconds=1.5)
    deferred = sim.of("line_deferred")
    assert deferred and deferred[0]["needs_s"] > deferred[0]["left_s"], deferred
    policy = sim.of("jit_policy")
    assert policy and policy[0]["floors"]["render"] > 0 and policy[0]["tau_s"] > 0
    assert all(line.id is not None for line in sim.lines)
    assert sim.measured["film"]["lines_landed"] == 1
