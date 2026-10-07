"""LTX-15 (D-13): a clip may cut to a new shot where the story allows
cuts. The cut is a per-clip choice of the Director (`ClipSpec.cut_to`),
compiled in LTX-2.5's prose convention ("The view cuts to …") after the
shot the picture is in; the clips after it open on the new shot, and the
next beat is told which shot the picture is in. A story without cuts
(the Lego story) stays one continuous take: a cut is dropped, and the
film's first clip never cuts."""

from __future__ import annotations

from src.models.story import Picture, Story
from src.story.compiler import compile_clip
from src.story.context import format_cut_rule
from src.story.schemas import BeatPlan, ClipSpec
from src.story.validator import apply_clip
from src.story.world import EventRecord, WorldModel
from tests.test_showrunner_clock import _beat, _clip, _engine
from tests.test_story_authority import Recording

LEGO = "stories/lego-void"
BALL = "close-up of the red ball at his feet"


def _world(*, cuts: bool, played: bool = True) -> WorldModel:
    world = WorldModel.from_story(Story.from_json(LEGO))
    world.perspective.cuts = cuts
    if played:
        world.events.append(EventRecord(segment=1, scene_id=0, narration="He stands.", on_screen=["lego_man"]))
    return world


def _cut_beat() -> BeatPlan:
    return BeatPlan(intent="he notices the ball", framing="medium shot, straight-on", clips=[
        ClipSpec(action="He looks down.", end_state="He looks at his feet.", on_screen=["lego_man"]),
        ClipSpec(cut_to=BALL, action="His claw hand closes around the ball.", end_state="The ball in his hand.",
                 on_screen=["lego_man"]),
        ClipSpec(action="He lifts the ball.", end_state="The ball at chest height.", on_screen=["lego_man"]),
    ])


def _compile(beat: BeatPlan, index: int, world: WorldModel) -> str:
    return compile_clip(beat, index, world, picture=Picture(style="Style.", anchor="Anchor."))


def test_a_cut_opens_on_the_shot_and_the_clips_after_it_continue_the_new_one():
    world = _world(cuts=True)
    beat = _cut_beat()
    before, cut, after = (_compile(beat, i, world) for i in range(3))
    assert before.startswith("Anchor. Medium shot, straight-on; the camera remains static.")
    assert cut.startswith("Anchor. Medium shot, straight-on. ")
    assert f"The view cuts to a {BALL}; the camera remains static. His claw hand closes around the ball." in cut
    assert "straight-on; the camera" not in cut, "the camera move belongs to the new shot"
    assert after.startswith("Anchor. Close-up of the red ball at his feet; the camera remains static."), after
    assert "cuts" not in before + after
    assert beat.clips[1].action_text() == f"Cut to {BALL}. His claw hand closes around the ball."


def test_a_shot_that_names_its_own_determiner_keeps_it():
    beat = _cut_beat()
    beat.clips[1].cut_to = "an extreme close-up of his hand."
    assert "The view cuts to an extreme close-up of his hand; " in _compile(beat, 1, _world(cuts=True))
    beat.clips[1].cut_to = "his face from below"
    assert "The view cuts to his face from below; " in _compile(beat, 1, _world(cuts=True))


def test_the_story_decides_whether_a_clip_may_cut():
    # One continuous take (the Lego story): the cut is dropped, the shot is the beat's.
    world = _world(cuts=False)
    beat = _cut_beat()
    for i in range(2):
        report = apply_clip(beat, i, world, segment_no=2 + i)
    assert beat.clips[1].cut_to == "" and world.shot == "medium shot, straight-on"
    assert any("one continuous take" in w for w in report.warnings)
    assert "cuts" not in _compile(beat, 1, world)

    # The film's first clip opens from the reference image: never a cut.
    world = _world(cuts=True, played=False)
    beat = BeatPlan(intent="open", framing="medium shot", clips=[ClipSpec(cut_to=BALL, action="a", end_state="b")])
    report = apply_clip(beat, 0, world, segment_no=1)
    assert beat.clips[0].cut_to == "" and any("first clip" in w for w in report.warnings)

    # Where cuts are allowed the cut is kept and the world follows the shot,
    # into the next beat's framing.
    world = _world(cuts=True)
    beat = _cut_beat()
    reports = [apply_clip(beat, i, world, segment_no=2 + i) for i in range(3)]
    assert world.shot == BALL and f"cut to {BALL}" in reports[1].applied and not reports[1].warnings
    nxt = BeatPlan(intent="he looks at it", framing=BALL, clips=[ClipSpec(action="a", end_state="b")])
    apply_clip(nxt, 0, world, segment_no=5)
    assert world.shot == BALL

    # The Director restating the framing in `cut_to` (what gpt-4.1 did in
    # the headless probe of 2026-09-24) is not a cut.
    world = _world(cuts=True)
    same = BeatPlan(intent="t", framing="medium shot, straight-on, eye level", clips=[
        ClipSpec(cut_to="Medium shot, straight-on, eye level.", action="a", end_state="b")])
    report = apply_clip(same, 0, world, segment_no=2)
    assert same.clips[0].cut_to == "" and any("already opens on" in w for w in report.warnings)
    assert "cuts" not in _compile(same, 0, world)


def test_the_director_is_told_the_story_rule():
    assert "The picture never cuts" in format_cut_rule(_world(cuts=False))
    assert "`cut_to` stays empty" in format_cut_rule(_world(cuts=False))
    world = _world(cuts=True)
    world.perspective.cutting = "Cut to the lamp when it flickers."
    rule = format_cut_rule(world)
    assert "This story allows cuts" in rule and "never on the film's first clip" in rule
    assert rule.endswith("Cut to the lamp when it flickers.")


class SystemRecording(Recording):
    def __init__(self, beats):
        super().__init__(beats)
        self.system_prompts: list[str] = []

    async def generate_structured(self, messages, schema, *args, **kwargs):
        if schema is BeatPlan:
            self.system_prompts.append(messages[0]["content"])
        return await super().generate_structured(messages, schema, *args, **kwargs)


async def _film(cuts: bool) -> tuple[SystemRecording, list[str], list[str]]:
    engine, _, state = _engine([])
    state.story.picture.perspective.cuts = cuts
    stub = SystemRecording([
        _beat("wake"),
        {**_beat("notice"), "clips": [_clip("He looks down."), _clip("His hand closes around it.", cut_to=BALL)]},
        _beat("hold"),
    ])
    engine.llm = engine.llm_director = engine.llm_showrunner = stub
    prompts, cameras = [], []
    for seg in range(1, 5):
        state.turn_number = seg
        if not engine.has_next_clip():
            await engine.plan(state)
        await engine.emit(state)
        prompts.append(state.current_shot.i2v_prompt)
        cameras.append(state.current_shot.camera)
    return stub, prompts, cameras


async def test_the_engine_carries_a_cut_to_the_picture_and_the_next_plan():
    stub, prompts, cameras = await _film(cuts=True)
    assert "This story allows cuts" in stub.system_prompts[0]
    assert f"The view cuts to a {BALL}" in prompts[2]
    assert cameras[:3] == ["medium shot", "medium shot", BALL]
    assert f"The picture is in this shot: {BALL}." in stub.director_prompts[-1]

    stub, prompts, cameras = await _film(cuts=False)
    assert "The picture never cuts" in stub.system_prompts[0]
    assert not any("cuts to" in p for p in prompts)
    assert not any("The picture is in this shot" in p for p in stub.director_prompts)
