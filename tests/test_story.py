"""Unit tests for the deterministic story layer: world model, validator,
compiler, pacing. No LLM involved."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.models.config import Config
from src.models.story import Story
from src.state.story_state import StoryState
from src.story.compiler import compile_prompt
from src.story.pacing import pacing_note, voiceover_gate
from src.story.schemas import DialogueLine, EntityOp, Segment, ShotSpec, ThreadOp
from src.story.validator import apply, check
from src.story.world import WorldModel


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


def _segment(**over) -> Segment:
    base = dict(
        narration="He does a thing.", outcome="A thing was done.", next_direction="More things.",
        shots=[ShotSpec(framing="medium shot", action_beats=["He waves."], on_screen=["lego_man"],
                        end_state="Lego Man waving")],
    )
    base.update(over)
    return Segment(**base)


def test_world_from_blueprint():
    w = _world()
    assert w.protagonist == "lego_man"
    assert w.cast_locked is True
    hero = w.entities["lego_man"]
    assert hero.speaks is False and hero.present is True and hero.location == "the_white_void"
    assert w.current_location().id == "the_white_void"
    assert w.resolve("Lego Man") is hero and w.resolve("lego man") is hero


def test_validator_introduce_update_remove():
    w = _world()
    seg = _segment(entity_ops=[
        EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball", status="at his feet"),
    ])
    seg.shots[0].on_screen = ["lego_man", "soccer ball"]
    r = apply(seg, w, segment_no=2)
    assert not r.violations and r.changed
    ball = w.entities["soccer_ball"]
    assert ball.present and ball.location == "the_white_void" and ball.first_seen == 2
    assert seg.shots[0].on_screen == ["lego_man", "soccer_ball"], "references normalised to ids"
    assert w.last_change == 2

    seg = _segment(entity_ops=[EntityOp(op="update", entity="soccer_ball", holder="lego_man")])
    r = apply(seg, w, segment_no=3)
    assert ball.holder == "lego_man" and w.held_props()[0] is ball and w.last_change == 3

    seg = _segment(entity_ops=[EntityOp(op="update", entity="soccer_ball", holder="nobody", present=False)])
    apply(seg, w, segment_no=4)
    assert ball.holder == "" and not ball.present and ball in w.dormant_props()

    seg = _segment(entity_ops=[EntityOp(op="remove", entity="soccer_ball", status="disassembled")])
    apply(seg, w, segment_no=5)
    assert ball.gone and ball not in w.dormant_props() and ball in w.gone_props()

    seg = _segment(entity_ops=[EntityOp(op="update", entity="ghost", status="x")])
    r = check(seg, w)
    assert any("unknown entity" in v for v in r.violations)

    # Locations resolve leniently; an unknown one is a warning, not a re-ask.
    seg = _segment(entity_ops=[EntityOp(op="update", entity="lego_man", location="at the_white_void")])
    r = check(seg, w)
    assert not r.violations and not r.warnings
    seg = _segment(entity_ops=[EntityOp(op="update", entity="lego_man", location="on_skateboard")])
    r = apply(seg, w, segment_no=6)
    assert not r.violations and any("not a known location" in x for x in r.warnings)
    assert w.entities["lego_man"].location == "the_white_void"


def test_validator_cast_lock_and_mute_dialogue():
    w = _world()
    seg = _segment(
        entity_ops=[EntityOp(op="introduce", entity="Lego Woman", kind="character", description="second figure")],
        dialogue=[DialogueLine(speaker="lego_man", line="Hi")],
    )
    r = check(seg, w)
    assert any("cast" in v and "locked" in v for v in r.violations)
    assert any("never speaks" in v for v in r.violations)
    apply(seg, w, segment_no=1)
    assert "lego_woman" not in w.entities, "locked cast: the character is never created"
    assert seg.dialogue == [], "mute dialogue is dropped on apply"

    seg = _segment(entity_ops=[EntityOp(op="introduce", entity="plastic dog", kind="prop", description="a small plastic dog")])
    r = apply(seg, w, segment_no=2)
    assert not r.violations and "plastic_dog" in w.entities, "creatures are props and allowed"


def test_validator_threads_and_interventions():
    w = _world()
    w.add_intervention(2, "kick the ball")
    seg = _segment(thread_ops=[ThreadOp(op="open", thread="the hat will come back", kind="setup")],
                   intervention="deferred", intervention_reason="needs a run-up")
    r = apply(seg, w, segment_no=2)
    assert w.open_threads()[0].id == "t1"
    item = w.interventions[0]
    assert item.disposition == "deferred" and item.resolved_at is None
    assert w.unresolved_interventions() == [item]

    seg = _segment(thread_ops=[ThreadOp(op="pay_off", thread="t1")], intervention="absorbed",
                   intervention_reason="here it is")
    r = apply(seg, w, segment_no=3)
    assert w.threads[0].status == "paid_off" and w.threads[0].closed_at == 3
    assert item.disposition == "absorbed" and item.resolved_at == 3
    assert any("deferred viewer request now absorbed" in a for a in r.applied)

    w.add_intervention(4, "do a flip")
    seg = _segment()  # no disposition given
    r = apply(seg, w, segment_no=4)
    assert w.interventions[-1].disposition == "deferred" and any("no disposition" in x for x in r.warnings)

    seg = _segment(thread_ops=[ThreadOp(op="drop", thread="t99")])
    r = check(seg, w)
    assert any("t99" in x for x in r.warnings) and not r.violations


def test_compiler_prompt():
    w = _world()
    apply(_segment(entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball")]), w, segment_no=1)
    seg = _segment(
        shots=[
            ShotSpec(transition="cut", framing="close-up on the ball", action_beats=["The ball wobbles."],
                     on_screen=["soccer_ball"], ambient_sound="a faint plastic rattle", end_state="the ball still", duration_seconds=3),
            ShotSpec(framing="wide shot", action_beats=["Lego Man runs in.", "He kicks."],
                     on_screen=["lego_man", "soccer_ball"], end_state="Lego Man mid-kick", duration_seconds=4),
        ],
        voiceover="A kick, at last.",
    )
    prompt, duration = compile_prompt(seg, w, visual_style="Studio product shot.", narration_mode="ltx", voiceover_style="warm")
    assert prompt.startswith("Studio product shot.")
    assert "A faint plastic rattle." in prompt and "Close-up on the ball, static camera." in prompt
    assert "Cut to a wide shot, static camera." in prompt
    assert "Lego Man (the most default LEGO minifigure" in prompt, "character re-identified from the sheet"
    assert "soccer ball (a studded LEGO ball)" in prompt
    assert "Final frame: Lego Man mid-kick." in prompt
    assert 'voice-over (English, warm) says: "A kick, at last."' in prompt
    assert duration == 7
    prompt_tts, _ = compile_prompt(seg, w, visual_style="", narration_mode="tts")
    assert "voice-over" not in prompt_tts, "in tts mode the line is not folded into the video prompt"
    _, fixed = compile_prompt(seg, w, visual_style="", narration_mode="off", clip_seconds=6)
    assert fixed == 6, "a fixed clip length overrides the shots' durations"


def test_pacing_gate_and_ladder():
    cfg = _config(min_pause_seconds=10)
    story = Story.from_json("data/story.json")
    st = StoryState.initialize(story, config_name="t")
    assert voiceover_gate(st, cfg) is True, "no line yet: opening line welcome"
    assert voiceover_gate(st, _config(narration="off")) is False
    w = _world()
    w.segment = 3; w.last_change = 3
    assert "moving" in pacing_note(w, _config(stagnation_threshold=5))
    w.segment = 9
    assert "development" in pacing_note(w, _config(stagnation_threshold=5))
    w.segment = 14
    assert "REQUIRED" in pacing_note(w, _config(stagnation_threshold=5))


def test_context_bounds_and_pinned_opening():
    from src.story.context import format_summaries, format_world_now, PRESENT_IN_FULL
    from src.story.world import Summary
    w = _world()
    for i in range(20):
        apply(_segment(entity_ops=[EntityOp(op="introduce", entity=f"prop {i}", description="x" * 300, status="s" * 300)]), w, segment_no=i + 1)
    text = format_world_now(w)
    assert text.count("\n- ") == PRESENT_IN_FULL, "the character plus the most recent props get full lines"
    assert "Also present (seen less recently)" in text
    assert "…" in text, "long status and look strings are clipped"
    w.summaries.append(Summary(scope="scene", first_segment=1, last_segment=10, text="He woke and looked around."))
    for k in range(5):
        w.summaries.append(Summary(scope="scene", first_segment=11 + k * 10, last_segment=20 + k * 10, text=f"scene {k}"))
    s = format_summaries(w)
    assert s.startswith("Opening (segments 1-10): He woke and looked around.")
    assert "scene 4" in s and "scene 0" not in s, "recent scenes shown, middle ones compacted away"


def test_compiler_cut_opens_from_reference_pose():
    w = _world()
    seg = _segment(shots=[ShotSpec(transition="cut", framing="wide shot", action_beats=["He waves."],
                                   on_screen=["lego_man"], end_state="e")])
    prompt, _ = compile_prompt(seg, w, visual_style="", narration_mode="off")
    assert "The shot opens with Lego Man standing upright in a wide product shot" in prompt
    seg.shots[0].transition = "continue"
    prompt, _ = compile_prompt(seg, w, visual_style="", narration_mode="off")
    assert "The shot opens with" not in prompt


def test_compiler_caps_described_entities():
    from src.story.compiler import DESCRIBED_IN_FULL
    w = _world()
    for i in range(10):
        apply(_segment(entity_ops=[EntityOp(op="introduce", entity=f"thing {i}", description=f"look {i}")]), w, segment_no=1)
    seg = _segment(shots=[ShotSpec(framing="wide", action_beats=["x"], on_screen=[f"thing_{i}" for i in range(10)] + ["lego_man"], end_state="e")])
    prompt, _ = compile_prompt(seg, w, visual_style="", narration_mode="off")
    assert prompt.count("(look ") == DESCRIBED_IN_FULL - 1, "character first, then props with looks up to the cap"
    assert "Lego Man (the most default" in prompt and "thing 9" in prompt


def test_persistence_round_trip():
    w = _world()
    apply(_segment(entity_ops=[EntityOp(op="introduce", entity="hat", description="a red cap")]), w, segment_no=1)
    from src.story.world import EventRecord
    w.events.append(EventRecord(segment=1, scene_id=1, narration="n", outcome="o"))
    with tempfile.TemporaryDirectory() as d:
        w.save(d); w.save(d)  # second save must not duplicate events
        loaded = WorldModel.load(d)
        assert "hat" in loaded.entities and len(loaded.events) == 1
        assert (Path(d) / "events.jsonl").read_text().count("\n") == 1
