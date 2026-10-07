"""The Showrunner's clock and memory, driven plan by plan with a stub LLM:
held replan flags are remembered (ARCH-02), a run is spawned after the
beat's first clip is applied (ARCH-03), the stagnation rungs have one
owner each (ARCH-05), threads show their age (ARCH-16) and notes are
kept only when they add something (ARCH-17)."""

from __future__ import annotations

import asyncio
import json

from src.graph.director_engine import DirectorEngine
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.story import Story
from src.state.story_state import StoryState
from src.story.context import format_threads
from src.story.pacing import REQUIRED_AT, SHOWRUNNER_REPLAN_AT, pacing_note
from src.story.schemas import BeatPlan, EntityNote, SceneCardOut, ShowrunnerUpdate
from src.story.validator import apply_showrunner
from src.story.world import MAX_NOTES, EventRecord, Summary, Thread, WorldModel


def _clip(action: str, **over) -> dict:
    base = {"action": action, "end_state": action, "camera_motion": "static", "ambient_sound": "",
            "on_screen": ["lego_man"], "dialogue": [], "entity_ops": []}
    base.update(over)
    return base


def _beat(action: str, *, clips: int = 1, **over) -> dict:
    base = {"intent": action, "framing": "medium shot", "clips": [_clip(f"{action} ({i + 1})") for i in range(clips)],
            "thread_ops": [], "intervention": "none", "intervention_reason": "",
            "scene_done": False, "needs_replan": False}
    base.update(over)
    return base


def _update(summary: str = "", replan: bool = True, goal: str = "next") -> dict:
    return {"scene_summary": summary, "act_summary": "", "entity_notes": [], "threads_to_drop": [],
            "arc": ["one act"], "current_act": 0, "replan": replan,
            "next_scene": {"goal": goal, "tension": "t", "exit_condition": "x", "intensity": "low",
                           "candidate_beats": []},
            "rationale": ""}


class Stub(LLMBackend):
    """Answers the Director from a list of beats (the last one repeats) and
    the Showrunner with a replan; records every Showrunner prompt."""

    def __init__(self, beats: list[dict]) -> None:
        self.model = "stub"
        self.beats = list(beats)
        self.showrunner_prompts: list[str] = []

    async def generate(self, messages, temperature=0.7, max_tokens=1024, *, trace_name=None, trace_metadata=None):
        return "{}", {}

    async def generate_structured(self, messages, schema, temperature=0.7, max_tokens=1024, *, strict=True,
                                  trace_name=None, trace_metadata=None):
        if schema is BeatPlan:
            beat = self.beats.pop(0) if len(self.beats) > 1 else self.beats[0]
            return json.dumps(beat), {}
        user = messages[-1]["content"]
        self.showrunner_prompts.append(user)
        bootstrap = "Trigger: bootstrap" in user
        return json.dumps(_update("" if bootstrap else f"summary {len(self.showrunner_prompts)}",
                                  goal=f"card {len(self.showrunner_prompts)}")), {}


def _engine(beats: list[dict], **over) -> tuple[DirectorEngine, Stub, StoryState]:
    cfg = dict(name="t", graph="director", llm_backend="mock", model="mock", compaction_interval=100,
               showrunner_min_gap=4, stagnation_seconds=90.0, scene_seconds=10_000)
    cfg.update(over)
    stub = Stub(beats)
    engine = DirectorEngine(llm=stub, config=Config(**cfg))
    state = StoryState.initialize(Story.from_json("stories/lego-void"), config_name="t")
    return engine, stub, state


async def _segment(engine: DirectorEngine, state: StoryState, seg: int) -> None:
    state.turn_number = seg
    if not engine.has_next_clip():
        await engine.plan(state)
    await engine.emit(state)
    await asyncio.sleep(0)          # let a spawned Showrunner task run


def _triggers(stub: Stub) -> list[str]:
    return [p.split("\n", 1)[0] for p in stub.showrunner_prompts]


async def test_a_held_replan_spawns_at_the_first_plan_past_the_gap():
    """ARCH-02: scene_done raised one segment after the bootstrap is inside
    the gap; it used to be noted and forgotten. It is re-raised at every
    plan and spawns at the first one past the gap, then it is cleared."""
    engine, stub, state = _engine([_beat("done", scene_done=True), _beat("quiet")])
    for seg in range(1, 8):
        await _segment(engine, state, seg)
    assert _triggers(stub) == ["Trigger: bootstrap: opening the film",
                               "Trigger: scene_done (held since segment 1)"], _triggers(stub)
    assert engine._held_trigger is None
    await engine.drain(state)
    assert engine.world.plan.scene.goal == "card 2" and engine.world.plan.scene.id == 2


async def test_a_new_card_clears_a_held_replan():
    """ARCH-02: a replan that lands (here a compaction run that decided to
    replan) answers the held flag; it is not raised again."""
    engine, stub, state = _engine([_beat("quiet")], showrunner_min_gap=1, compaction_interval=1)
    await _segment(engine, state, 1)
    engine._held_trigger = ("needs_replan", "replan", 1)   # as if raised while a run was in flight
    engine._land(ShowrunnerUpdate(**_update("s", replan=True)), at_segment=1, force_replan=False, reason="compaction")
    assert engine._held_trigger is None


async def test_the_showrunner_snapshot_includes_the_clip_that_triggered_it():
    """ARCH-03: the run is spawned after the beat's first clip is applied,
    so the snapshot has that clip's ops and the compaction windows are
    contiguous and include the spawn segment."""
    hat = _beat("a hat appears", scene_done=True, clips=2)
    hat["clips"][0]["entity_ops"] = [{"op": "introduce", "entity": "red hat", "kind": "prop",
                                      "description": "a red cap", "status": "on the floor", "location": "",
                                      "holder": "", "present": True, "posture": "", "activity": "",
                                      "position": ""}]
    engine, stub, state = _engine([_beat("wake"), _beat("look"), hat, _beat("quiet", scene_done=True),
                                   _beat("quiet")], showrunner_min_gap=2)
    for seg in range(1, 12):
        await _segment(engine, state, seg)
    await engine.drain(state)
    spawn = next(p for p in stub.showrunner_prompts if p.startswith("Trigger: scene_done"))
    assert "introduce prop red_hat" in spawn and "a hat appears (1)" in spawn, "the spawn clip is in the window"
    assert "a hat appears (2)" not in spawn, "the next clip of the beat is not"
    windows = [(s.first_segment, s.last_segment) for s in engine.world.summaries]
    assert windows[0] == (1, 3), windows
    assert all(b[0] == a[1] + 1 for a, b in zip(windows, windows[1:])), f"contiguous: {windows}"
    spawned_at = [e.segment for e in engine.world.events if e.beat_intent == "a hat appears" and e.clip_index == 1]
    assert windows[0][1] == spawned_at[0]


async def test_a_spawn_waits_for_the_first_clip_and_survives_a_cut():
    """ARCH-03 with a cut: a beat dropped before its first clip spawns
    nothing; its replan flag stays held and the next plan's first clip
    spawns it."""
    engine, stub, state = _engine([_beat("wake"), _beat("done", scene_done=True, clips=2), _beat("quiet")],
                                  showrunner_min_gap=1)
    await _segment(engine, state, 1)
    state.turn_number = 2
    await engine.plan(state)
    assert engine._pending_spawn is not None and len(stub.showrunner_prompts) == 1, "not spawned at the plan"
    engine.abort_beat("the viewer spoke")
    assert engine._pending_spawn is None and engine._held_trigger is not None
    await _segment(engine, state, 2)
    assert _triggers(stub)[-1] == "Trigger: scene_done (held since segment 2)", _triggers(stub)
    assert engine._held_trigger is None


def test_stagnation_rungs_have_one_owner_each():
    """ARCH-05: the Director is told a shift is REQUIRED from 1.5x; the
    Showrunner's stagnation replan fires only from 2x."""
    assert (REQUIRED_AT, SHOWRUNNER_REPLAN_AT) == (1.5, 2.0)
    engine, _, state = _engine([_beat("quiet")], showrunner_min_gap=1)
    world = WorldModel.from_story(state.story)
    engine.world = world
    quiet = BeatPlan(**_beat("quiet"))
    for seconds, required, replan in ((100, False, False), (140, True, False), (175, True, False), (185, True, True)):
        world.events = [EventRecord(segment=i + 1, scene_id=1, narration="x", duration=5)
                        for i in range(seconds // 5)]
        world.segment = len(world.events)
        note = pacing_note(world, engine.config)
        reason, _ = engine._trigger(quiet, False)
        assert ("REQUIRED" in note) is required, (seconds, note)
        assert (reason is not None and reason.startswith("stagnation")) is replan, (seconds, reason)


def test_threads_show_their_age():
    """ARCH-16: each open thread shows where it opened and how long ago,
    in segments and story seconds; nothing expires it."""
    world = WorldModel(title="t")
    world.events = [EventRecord(segment=i, scene_id=1, narration="x", duration=6) for i in range(1, 41)]
    world.segment = 40
    world.threads = [Thread(id="t1", text="the ball wants a kick", opened_at=10),
                     Thread(id="t2", text="a hat", kind="gag", opened_at=40)]
    text = format_threads(world)
    assert "- `t1` [setup, opened segment 10, 30 segments, 186 s of film ago] the ball wants a kick" in text, text
    assert "[gag, opened segment 40, 0 segments, 6 s of film ago]" in text
    assert all(t.status == "open" for t in world.threads)


async def test_the_showrunner_reads_the_film_and_how_it_opens():
    """The Showrunner's system prompt carries the film as the Director reads
    it plus how the film opens (it writes the first card from it), the
    truths, and the authority rule; an empty field adds no text."""
    from src.agents import showrunner as showrunner_agent
    story = Story.from_json("stories/lego-void")
    seen: list[str] = []

    class Capture(Stub):
        async def generate_structured(self, messages, schema, **kw):
            seen.append(messages[0]["content"])
            return await super().generate_structured(messages, schema, **kw)

    config = Config(name="t", graph="director", llm_backend="mock", model="mock")
    for teller in (story.storyteller, story.storyteller.model_copy(update={"temperament": ""})):
        state = StoryState.initialize(story.model_copy(update={"storyteller": teller}), config_name="t")
        await showrunner_agent.run(world=WorldModel.from_story(state.story), state=state, llm=Capture([_beat("x")]),
                                   config=config, reason="compaction", mode="compact")
    full, bare = seen
    assert "How the film opens: He wakes in the empty white space" in full
    assert "The question the film keeps open: Does he ever land the perfect bit?" in full
    assert "How this storyteller takes the unexpected: Say yes" in full and "takes the unexpected" not in bare
    assert "# What is true in this story (absolute)\n\n- This is a LEGO world" in full
    assert "outranks your scene card" in full, "the authority rule (ARCH-01)"


def test_notes_are_kept_only_when_they_add_something():
    """ARCH-17: props keep no notes; a note that restates the cast sheet or
    is already in a summary is not kept; a character keeps at most
    MAX_NOTES, the oldest going first."""
    world = WorldModel.from_story(Story.from_json("stories/lego-void"))
    from src.story.world import Entity
    world.entities["ball"] = Entity(id="ball", name="ball", kind="prop", present=True)
    world.summaries = [Summary(scope="scene", first_segment=1, last_segment=10,
                               text="He found a ball. He can balance it on his head. The void stayed quiet.")]

    def land(*pairs: tuple[str, str], summary: str = "") -> list[str]:
        update = ShowrunnerUpdate(scene_summary=summary, replan=False,
                                  next_scene=SceneCardOut(goal="g", tension="t", exit_condition="x"),
                                  entity_notes=[EntityNote(entity=e, note=n) for e, n in pairs])
        return apply_showrunner(world, update, at_segment=world.last_compacted + 1, force_replan=False)

    # Session A's bootstrap note, verbatim in shape: the starting pose plus the look.
    restated = ("Currently standing upright in the center of the frame, arms at his sides, feet together, facing "
                "the camera. The most default LEGO minifigure: bare yellow head with no hair piece, printed only with "
                "the classic two-dot eyes and curved smile, plain red torso with no print, plain blue legs, claw hands.")
    out = land(("lego_man", restated), ("ball", "It squeaks when kicked."),
               ("lego_man", "He can balance it on his head."), ("lego_man", "He is afraid of the dark edge."),
               ("lego_man", "He keeps losing the ball off screen right."), summary="He keeps losing the ball off screen right. Then he sits down.")
    hero = world.entities["lego_man"]
    assert hero.notes == ["He is afraid of the dark edge."], hero.notes
    assert world.entities["ball"].notes == []
    joined = " ".join(out)
    assert "restates the cast description" in joined and "only characters keep notes" in joined
    assert joined.count("contained in a summary") == 2, out
    land(("lego_man", "He is afraid of the dark edge."))
    assert hero.notes == ["He is afraid of the dark edge."], "a duplicate is not kept twice"
    for i in range(MAX_NOTES + 1):
        land(("lego_man", f"Fact number {i} about his juggling habit."))
    assert len(hero.notes) == MAX_NOTES and hero.notes[0] == "Fact number 1 about his juggling habit.", hero.notes
