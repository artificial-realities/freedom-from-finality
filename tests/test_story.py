"""Unit tests for the deterministic story layer: world model, validator,
compiler, pacing. No LLM involved."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.models.config import Config
from src.models.story import Story
from src.state.story_state import StoryState
from src.story.compiler import compile_clip
from src.story.pacing import pacing_note, scene_note, voiceover_gate
from src.story.schemas import BeatPlan, ClipSpec, DialogueLine, EntityOp, ThreadOp
from src.story.validator import apply_clip, check
from src.story.world import EventRecord, WorldModel


def _config(**over) -> Config:
    base = dict(name="t", graph="director", llm_backend="mock", model="mock")
    base.update(over)
    return Config(**base)


def _world() -> WorldModel:
    story = Story.from_json("data/story.json")
    return WorldModel.from_blueprint(
        title=story.title, characters=story.characters, locations=story.locations,
        cast_locked=story.cast_locked,
    )


def _clip(**over) -> ClipSpec:
    base = dict(action="He waves.", end_state="Lego Man waving", on_screen=["lego_man"])
    base.update(over)
    return ClipSpec(**base)


def _beat(**over) -> BeatPlan:
    base = dict(intent="a wave", framing="medium shot", clips=[_clip()])
    base.update(over)
    return BeatPlan(**base)


def _apply(beat: BeatPlan, w: WorldModel, segment_no: int, index: int = 0):
    return apply_clip(beat, index, w, segment_no=segment_no)


def test_world_from_blueprint():
    w = _world()
    assert w.protagonist == "lego_man"
    assert w.cast_locked is True
    hero = w.entities["lego_man"]
    assert hero.speaks is False and hero.present is True and hero.location == "the_white_void"
    assert hero.look_short.startswith("the same plain LEGO minifigure"), "the blueprint's short look carries over"
    assert w.current_location().id == "the_white_void"
    assert w.resolve("Lego Man") is hero and w.resolve("lego man") is hero


def test_validator_introduce_update_remove_and_character_state():
    w = _world()
    beat = _beat(clips=[_clip(on_screen=["lego_man", "soccer ball"], entity_ops=[
        EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball", status="at his feet"),
        EntityOp(op="update", entity="lego_man", kind="character", posture="crouching", activity="eyeing the ball", position="centre frame"),
    ])])
    r = _apply(beat, w, 2)
    assert not r.violations and r.changed
    ball = w.entities["soccer_ball"]
    assert ball.present and ball.location == "the_white_void" and ball.first_seen == 2
    assert beat.clips[0].on_screen == ["lego_man", "soccer_ball"], "references normalised to ids"
    hero = w.entities["lego_man"]
    assert hero.now() == "crouching; eyeing the ball; centre frame"
    assert w.last_change == 2

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="soccer_ball", holder="lego_man")])])
    r = _apply(beat, w, 3)
    assert ball.holder == "lego_man" and w.held_props()[0] is ball and w.last_change == 3

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="lego_man", kind="character", holder="lego_man", posture="standing")])])
    r = _apply(beat, w, 4)
    assert hero.holder == "" and hero.posture == "standing", "a character is never held; the posture still lands"
    assert any("cannot be held" in x or "holder on character" in x for x in r.warnings)

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="soccer_ball", holder="nobody", present=False)])])
    _apply(beat, w, 5)
    assert ball.holder == "" and not ball.present and ball in w.dormant_props()

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="remove", entity="soccer_ball", status="disassembled")])])
    _apply(beat, w, 6)
    assert ball.gone and ball not in w.dormant_props() and ball in w.gone_props()

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="ghost", status="x")])])
    r = check(beat, w)
    assert any("unknown entity" in v for v in r.violations)

    # Locations resolve leniently; an unknown one is a warning, not a re-ask.
    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="lego_man", location="at the_white_void")])])
    r = check(beat, w)
    assert not r.violations and not r.warnings
    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="lego_man", location="on_skateboard")])])
    r = check(beat, w)
    assert not r.violations and any("not a known location" in x for x in r.warnings)


def test_validator_checks_every_clip_and_caps_them():
    w = _world()
    beat = _beat(clips=[
        _clip(entity_ops=[EntityOp(op="introduce", entity="hat", description="a red cap")]),
        _clip(entity_ops=[EntityOp(op="update", entity="hat", holder="lego_man")]),  # introduced in clip 1: fine
        _clip(entity_ops=[EntityOp(op="update", entity="nothing", status="x")]),
    ])
    r = check(beat, w, max_clips=4)
    assert len(r.violations) == 1 and r.violations[0].startswith("clip 3")
    r = check(beat, w, max_clips=2)
    assert not r.violations and any("only the first 2" in x for x in r.warnings), "clips past the cap are not checked"


def test_validator_cast_lock_and_mute_dialogue():
    w = _world()
    beat = _beat(clips=[_clip(
        entity_ops=[EntityOp(op="introduce", entity="Lego Woman", kind="character", description="second figure")],
        dialogue=[DialogueLine(speaker="lego_man", line="Hi")],
    )])
    r = check(beat, w)
    assert any("cast" in v and "locked" in v for v in r.violations)
    assert any("never speaks" in v for v in r.violations)
    _apply(beat, w, 1)
    assert "lego_woman" not in w.entities, "locked cast: the character is never created"
    assert beat.clips[0].dialogue == [], "mute dialogue is dropped on apply"

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="plastic dog", kind="prop", description="a small plastic dog")])])
    r = _apply(beat, w, 2)
    assert not r.violations and "plastic_dog" in w.entities, "creatures are props and allowed"


def test_validator_threads_and_interventions_land_on_the_first_clip():
    w = _world()
    w.add_intervention(2, "kick the ball")
    beat = _beat(clips=[_clip(), _clip(action="He waits.")],
                 thread_ops=[ThreadOp(op="open", thread="the hat will come back", kind="setup")],
                 intervention="deferred", intervention_reason="needs a run-up")
    r = _apply(beat, w, 2, index=1)
    assert not w.open_threads() and w.interventions[0].disposition == "pending", "clip 2 lands neither threads nor the disposition"
    r = _apply(beat, w, 2, index=0)
    assert w.open_threads()[0].id == "t1"
    item = w.interventions[0]
    assert item.disposition == "deferred" and item.resolved_at is None
    assert w.unresolved_interventions() == [item]

    beat = _beat(thread_ops=[ThreadOp(op="pay_off", thread="t1")], intervention="absorbed",
                 intervention_reason="here it is")
    r = _apply(beat, w, 3)
    assert w.threads[0].status == "paid_off" and w.threads[0].closed_at == 3
    assert item.disposition == "absorbed" and item.resolved_at == 3
    assert any("deferred viewer request now absorbed" in a for a in r.applied)

    w.add_intervention(4, "do a flip")
    beat = _beat()  # no disposition given
    r = _apply(beat, w, 4)
    assert w.interventions[-1].disposition == "deferred" and any("no disposition" in x for x in r.warnings)

    beat = _beat(thread_ops=[ThreadOp(op="drop", thread="t99")])
    r = check(beat, w)
    assert any("t99" in x for x in r.warnings) and not r.violations

    assert w.drop_pending_interventions(4) == 0, "nothing pending at 4 any more"
    w.add_intervention(5, "a line the cancelled call recorded")
    assert w.drop_pending_interventions(5) == 1 and not w.unresolved_interventions()[1:]


def test_compiler_opening_clip_carries_style_look_and_pose():
    w = _world()
    beat = _beat(clips=[_clip(action="He waves.", ambient_sound="a faint plastic rattle", end_state="Lego Man mid-wave")],
                 voiceover="A kick, at last.")
    prompt = compile_clip(beat, 0, w, visual_style="Studio product shot. Pure white.", visual_anchor="Studio shot, white.",
                          narration_mode="ltx", voiceover_style="warm", opening=True, segment_no=1)
    assert prompt.startswith("Studio product shot. Pure white.")
    assert "Lego Man (the most default LEGO minifigure" in prompt, "the full look on the opening clip"
    assert "The shot opens with Lego Man standing upright in a wide product shot" in prompt
    assert "A faint plastic rattle." in prompt and "Medium shot, static camera." in prompt
    assert "Final frame: Lego Man mid-wave." in prompt
    assert 'voice-over (English, warm) says: "A kick, at last."' in prompt


def test_compiler_continuing_clip_is_compact():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball")])]), w, 1)
    beat = _beat(clips=[
        _clip(action="He kicks.", end_state="mid-kick", on_screen=["lego_man", "soccer_ball"]),
        _clip(action="He watches the ball roll.", end_state="watching", on_screen=["lego_man", "soccer_ball"], camera_motion="slow push in"),
    ], voiceover="A kick, at last.")
    style = "Studio product shot. Pure white cyclorama."
    p1 = compile_clip(beat, 0, w, visual_style=style, visual_anchor="Studio shot, white.", narration_mode="ltx", segment_no=2)
    assert p1.startswith("Studio shot, white.") and "Pure white cyclorama" not in p1, "the anchor replaces the style paragraph"
    assert "Lego Man (the same plain LEGO minifigure" in p1 and "the most default" not in p1, "short look on continues"
    assert "soccer ball;" in p1 or "soccer ball." in p1
    assert "(a studded LEGO ball)" not in p1, "a prop already introduced is named, not described"
    assert "The shot opens with" not in p1 and "Cut to" not in p1
    assert "voice-over" in p1
    p2 = compile_clip(beat, 1, w, visual_style=style, visual_anchor="", narration_mode="ltx", segment_no=3)
    assert p2.startswith("Studio product shot.") and "cyclorama" not in p2, "no anchor: the style's first sentence"
    assert "voice-over" not in p2, "the line plays over the first clip only"
    assert "Medium shot, slow push in camera." in p2
    # The clip that introduces a prop carries its description.
    beat3 = _beat(clips=[_clip(action="A cone appears.", end_state="cone", on_screen=["lego_man", "cone"],
                               entity_ops=[EntityOp(op="introduce", entity="cone", description="a yellow LEGO cone")])])
    _apply(beat3, w, 4)
    p3 = compile_clip(beat3, 0, w, visual_style=style, narration_mode="off", segment_no=4)
    assert "cone (a yellow LEGO cone)" in p3
    p_tts = compile_clip(beat, 0, w, visual_style="", narration_mode="tts", segment_no=2)
    assert "voice-over" not in p_tts, "in tts mode the line is not folded into the video prompt"


def test_pacing_in_story_seconds():
    cfg = _config(min_pause_seconds=10)
    story = Story.from_json("data/story.json")
    st = StoryState.initialize(story, config_name="t")
    assert voiceover_gate(st, cfg) is True, "no line yet: opening line welcome"
    assert voiceover_gate(st, _config(narration="off")) is False
    w = _world()
    for seg in range(1, 41):
        w.events.append(EventRecord(segment=seg, scene_id=1, narration="n", duration=6))
    w.segment = 40
    w.last_change = 40
    cfg = _config(stagnation_seconds=90, scene_seconds=240)
    assert "no pacing pressure" in pacing_note(w, cfg)
    w.last_change = 20                     # 20 clips × 6 s = 120 s quiet
    assert "development" in pacing_note(w, cfg)
    w.last_change = 5                      # 210 s quiet
    assert "REQUIRED" in pacing_note(w, cfg)
    from src.story.world import SceneCard
    w.plan.scene = SceneCard(id=1, goal="g", tension="t", exit_condition="e", opened_at=1)
    w.scene_started = 1                    # 240 s so far
    assert "past its intended length" in scene_note(w, cfg)
    w.scene_started = 21                   # 120 s
    assert "there is time" in scene_note(w, cfg)


def test_context_shows_character_state_and_keeps_notes_out_of_the_cast():
    from src.story.context import format_cast, format_recent, format_summaries, format_world_now, PRESENT_IN_FULL
    from src.story.world import Summary
    w = _world()
    hero = w.entities["lego_man"]
    hero.notes.append("He is proud of the kick.")
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="lego_man", kind="character",
                                                   posture="lying on his back", activity="asleep", position="centre frame")])]), w, 1)
    text = format_world_now(w)
    assert "now: lying on his back; asleep; centre frame" in text
    assert "notes: He is proud of the kick." in text
    assert "He is proud" not in format_cast(w), "notes live in the user prompt so the system prompt stays cacheable"
    for i in range(20):
        _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity=f"prop {i}", description="x" * 300, status="s" * 300)])]), w, i + 2)
    text = format_world_now(w)
    assert text.count("\n- ") == PRESENT_IN_FULL, "the character plus the most recent props get full lines"
    assert "Also present (seen less recently)" in text
    assert "…" in text, "long status and look strings are clipped"
    w.events.append(EventRecord(segment=1, scene_id=1, beat=1, beat_intent="he sleeps", clip_index=2, clip_count=3,
                                narration="He lies still.", end_state="asleep", duration=6))
    recent = format_recent(w, 4)
    assert '[segment 1 · beat 1 "he sleeps" clip 2/3] He lies still.' in recent
    assert "Direction set after" not in recent, "no next_direction feedback loop"
    w.summaries.append(Summary(scope="scene", first_segment=1, last_segment=10, text="He woke and looked around."))
    for k in range(5):
        w.summaries.append(Summary(scope="scene", first_segment=11 + k * 10, last_segment=20 + k * 10, text=f"scene {k}"))
    s = format_summaries(w)
    assert s.startswith("Opening (segments 1-10): He woke and looked around.")
    assert "scene 4" in s and "scene 0" not in s, "recent scenes shown, middle ones compacted away"


def test_compiler_caps_described_entities():
    from src.story.compiler import DESCRIBED_IN_FULL
    w = _world()
    for i in range(10):
        _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity=f"thing {i}", description=f"look {i}")])]), w, 1)
    beat = _beat(clips=[_clip(action="x", on_screen=[f"thing_{i}" for i in range(10)] + ["lego_man"], end_state="e")])
    prompt = compile_clip(beat, 0, w, visual_style="", narration_mode="off", segment_no=1)
    assert prompt.count("(look ") == DESCRIBED_IN_FULL - 1, "character first, then props with looks up to the cap"
    assert "Lego Man (" in prompt and "thing 9" in prompt


def test_persistence_round_trip():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="hat", description="a red cap")])]), w, 1)
    w.events.append(EventRecord(segment=1, scene_id=1, narration="n", beat=1, beat_intent="a hat"))
    with tempfile.TemporaryDirectory() as d:
        w.save(d); w.save(d)  # second save must not duplicate events
        loaded = WorldModel.load(d)
        assert "hat" in loaded.entities and len(loaded.events) == 1 and loaded.events[0].beat_intent == "a hat"
        assert (Path(d) / "events.jsonl").read_text().count("\n") == 1
