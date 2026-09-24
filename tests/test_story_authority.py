"""Story authority after X-07/X-08 (D-01): the viewer's direction outranks
the scene card while it is active. A card clears the direction only if
its Showrunner run was triggered after the line, so the replan the line
itself provokes (spawned at once, or held by the gap and spawned later)
does not erase it; the direction expires after `viewer_momentum_seconds`
of film (ARCH-08). While it is active the Director sees the scene card
paused (ARCH-09 with D-05); the Showrunner always sees it whole."""

from __future__ import annotations

import json

from src.models.config import Config
from src.models.story import Story
from src.story.context import format_plan, viewer_direction_active
from src.story.schemas import BeatPlan, ShowrunnerUpdate
from src.story.validator import apply_showrunner
from src.story.world import SceneCard, WorldModel
from tests.test_showrunner_clock import Stub, _beat, _engine, _segment, _triggers, _update

LINE = "a paper boat drifts in"


class Recording(Stub):
    """The clock tests' stub, recording the Director's prompts too, with
    cards that carry candidate beats."""

    def __init__(self, beats):
        super().__init__(beats)
        self.director_prompts: list[str] = []

    async def generate_structured(self, messages, schema, *args, **kwargs):
        if schema is BeatPlan:
            self.director_prompts.append(messages[-1]["content"])
            return await super().generate_structured(messages, schema, *args, **kwargs)
        user = messages[-1]["content"]
        self.showrunner_prompts.append(user)
        n = len(self.showrunner_prompts)
        update = _update("" if "Trigger: bootstrap" in user else f"summary {n}", goal=f"card {n}")
        update["next_scene"]["candidate_beats"] = [f"card {n} idea A", f"card {n} idea B"]
        return json.dumps(update), {}


def _answer(**over) -> dict:
    return _beat("he boards the boat", intervention="absorbed", **over)


async def _film(beats, lines: dict[int, str], segments: int, **cfg):
    engine, _, state = _engine([], **cfg)
    stub = Recording(beats)
    engine.llm = engine.llm_director = engine.llm_showrunner = stub
    for seg in range(1, segments + 1):
        state.user_input = lines.get(seg, "")
        if state.user_input:
            engine.abort_beat("the viewer spoke")
        await _segment(engine, state, seg)
    return engine, stub, state


async def test_the_lines_own_replan_does_not_clear_it_even_when_held():
    """X-07's base run: a line at segment 28 raised needs_replan, the gap
    held it, it spawned at 32 and its card erased the line. Now the card
    lands and the direction stays."""
    engine, stub, state = await _film(
        [_beat("wake"), _answer(needs_replan=True), _beat("sail")], {2: LINE}, 9, showrunner_min_gap=4)
    assert _triggers(stub)[1] == "Trigger: needs_replan (held since segment 2)", _triggers(stub)
    assert engine.world.plan.scene.id == 2, "the held replan's card landed"
    assert engine.world.viewer_momentum == LINE and engine.world.viewer_momentum_segment == 2


async def test_the_lines_own_replan_spawned_at_once_does_not_clear_it():
    engine, stub, state = await _film(
        [_beat("wake"), _answer(needs_replan=True), _beat("sail")], {2: LINE}, 6,
        showrunner_min_gap=1)
    assert _triggers(stub)[1] == "Trigger: needs_replan", _triggers(stub)
    assert engine.world.plan.scene.id == 2 and engine.world.viewer_momentum == LINE


async def test_a_card_triggered_after_the_line_clears_it():
    engine, stub, state = await _film(
        [_beat("wake"), _answer(), _beat("sail"), _beat("sail"), _beat("docked", scene_done=True), _beat("rest")],
        {2: LINE}, 9, showrunner_min_gap=1)
    assert _triggers(stub)[1] == "Trigger: scene_done", _triggers(stub)
    assert engine.world.plan.scene.id == 2
    assert engine.world.viewer_momentum == "" and engine.world.viewer_momentum_segment == 0


async def test_the_direction_expires_after_its_story_seconds():
    """With 6.04-s clips and a 13-s expiry the line of segment 2 is active
    for the plans of segments 3 and 4 and gone at segment 5's."""
    engine, stub, state = await _film(
        [_beat("wake"), _answer(), _beat("sail")], {2: LINE}, 6, viewer_momentum_seconds=13.0)
    assert engine.world.viewer_momentum == ""
    by_segment = dict(zip(range(1, 7), stub.director_prompts))
    block = "The viewer's last direction (segment 2)"
    assert all(block in by_segment[s] for s in (3, 4)), "active at segments 3 and 4 (6.0 and 12.1 s)"
    assert block not in by_segment[5] and f'segment 2: "{LINE}" → absorbed' in by_segment[5], "expired; still handled"


async def test_the_director_sees_the_card_paused_while_a_direction_is_active():
    """D-01 hid the candidates; D-05 pauses the whole card (goal, tension,
    exit condition, candidates) until the direction expires."""
    engine, stub, state = await _film(
        [_beat("wake"), _answer(), _beat("sail")], {2: LINE}, 6, viewer_momentum_seconds=13.0)
    paused = "paused while the viewer's direction is active"
    by_segment = dict(zip(range(1, 7), stub.director_prompts))
    shown = lambda s: "goal: card 1" in by_segment[s] and "card 1 idea A" in by_segment[s]  # noqa: E731
    assert shown(1) and paused not in by_segment[1], "quiet film: the card is shown"
    for s in (2, 3, 4):
        assert paused in by_segment[s] and "goal: card 1" not in by_segment[s] and "idea A" not in by_segment[s], s
    assert shown(5) and paused not in by_segment[5], "expired: the card is back, whole"


def test_the_showrunner_always_sees_its_candidates():
    world = WorldModel.from_story(Story.from_json("stories/lego-void"))
    world.plan.scene = SceneCard(id=1, goal="g", tension="t", exit_condition="x",
                                 candidate_beats=["idea A"], opened_at=1)
    world.viewer_momentum, world.viewer_momentum_segment = LINE, 3
    cfg = Config(name="t", llm_backend="mock", model="m")
    assert viewer_direction_active(world) and viewer_direction_active(world, "") is True
    assert "idea A" in format_plan(world, cfg) and "goal: g" in format_plan(world, cfg), "the Showrunner's copy (the default)"
    paused = format_plan(world, cfg, paused=True)
    assert "idea A" not in paused and "goal: g" not in paused and "paused while the viewer's direction is active" in paused
    world.viewer_momentum = ""
    assert not viewer_direction_active(world) and viewer_direction_active(world, "go left")


def test_apply_showrunner_keys_the_clear_to_the_trigger():
    world = WorldModel.from_story(Story.from_json("stories/lego-void"))
    world.viewer_momentum, world.viewer_momentum_segment = LINE, 5
    update = ShowrunnerUpdate(**_update("s", replan=True))
    apply_showrunner(world, update, at_segment=7, force_replan=True, triggered_at=5)
    assert world.viewer_momentum == LINE, "raised by the line's own beat, spawned two segments later"
    apply_showrunner(world, update, at_segment=9, force_replan=True, triggered_at=6)
    assert world.viewer_momentum == "", "raised after the line"
    world.viewer_momentum, world.viewer_momentum_segment = LINE, 5
    apply_showrunner(world, update, at_segment=6, force_replan=True)
    assert world.viewer_momentum == "", "no trigger given: the spawn segment decides, as before"
