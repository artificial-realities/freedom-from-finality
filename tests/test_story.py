"""Unit tests for the deterministic story layer: world model, validator,
compiler, pacing. No LLM involved."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.models.config import Config
from src.models.story import Picture, Story
from src.story.compiler import compile_clip
from src.story.pacing import pacing_note, scene_note
from src.story.schemas import BeatPlan, ClipSpec, DialogueLine, EntityOp, ThreadOp
from src.story.validator import apply_clip, check
from src.story.world import EventRecord, WorldModel


def _config(**over) -> Config:
    base = dict(name="t", graph="director", llm_backend="mock", model="mock")
    base.update(over)
    return Config(**base)


PLAIN = Picture()   # no style, no reference: the compiler's own words only


def _world(**cast) -> WorldModel:
    story = Story.from_json("stories/lego-void")
    if cast:
        story = story.model_copy(update={"cast": story.cast.model_copy(update=cast)})
    return WorldModel.from_story(story)


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
    assert not w.cast_locked and w.newcomers.startswith("Other minifigures from any LEGO theme")
    hero, spaceman = w.entities["lego_man"], w.entities["spaceman"]
    assert hero.present is True and hero.location == "the_white_void"
    assert hero.description.startswith("The most default minifigure") and hero.wants == "Something to do, and to get it right."
    assert hero.voice.description.startswith("earnest") and hero.now() == "standing upright, arms at his sides; centre frame"
    assert hero.look_short.startswith("the same plain LEGO minifigure"), "the blueprint's short look carries over"
    assert not spaceman.present and spaceman.appears == "must" and spaceman.location == ""
    assert w.opening_picture == ["lego_man"], "the reference image shows Lego Man, not the Spaceman"
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


def test_validator_cast_lock_and_speakers():
    """A story without newcomers keeps its cast; any character may speak,
    a prop may not."""
    w = _world(newcomers="")
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="plastic dog", description="a plastic dog")])]), w, 1)
    beat = _beat(clips=[_clip(
        entity_ops=[EntityOp(op="introduce", entity="Lego Woman", kind="character", description="second figure")],
        dialogue=[DialogueLine(speaker="lego_man", line="Hi"), DialogueLine(speaker="plastic_dog", line="Woof, said the dog")],
    )])
    r = check(beat, w)
    assert any("allows no characters beyond its cast" in v for v in r.violations)
    assert any("dialogue speaker 'plastic_dog' is not a known character id" in v for v in r.violations)
    assert not any("speaker 'lego_man'" in v for v in r.violations), "every character may speak"
    _apply(beat, w, 2)
    assert "lego_woman" not in w.entities, "locked cast: the character is never created"
    assert [d.speaker for d in beat.clips[0].dialogue] == ["lego_man"], "the prop's line is dropped on apply"

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="plastic cat", kind="prop", description="a small plastic cat")])])
    r = _apply(beat, w, 3)
    assert not r.violations and "plastic_cat" in w.entities, "creatures are props and allowed"


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
    assert any("deferred viewer request (segment 2) now absorbed" in a for a in r.applied), \
        "the only request owed is resolved without the field"

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


def test_compiler_opening_clip_carries_style_look_and_reference():
    w = _world()
    reference = Story.from_json("stories/lego-void").picture.reference
    picture = Picture(style="Studio product shot. Pure white.", anchor="Studio shot, white.", reference=reference)
    beat = _beat(clips=[_clip(action="He waves.", ambient_sound="a faint plastic rattle", end_state="Lego Man mid-wave",
                              dialogue=[DialogueLine(speaker="lego_man", line="Hello?")])])
    prompt = compile_clip(beat, 0, w, picture=picture, opening=True, segment_no=1)
    assert prompt.startswith("Studio product shot. Pure white.")
    assert "Lego Man (the most default LEGO minifigure" in prompt, "the full look on the opening clip"
    assert "The shot opens with Lego Man standing upright in a wide product shot" in prompt
    assert "A faint plastic rattle." in prompt and "Medium shot; the camera remains static." in prompt
    assert "Entering the picture" not in prompt, "the reference image already shows the character"
    assert 'Lego Man says in a earnest, slightly formal, a little breathless voice, in English, "Hello?"' in prompt
    assert "Final frame: Lego Man mid-wave." in prompt


def test_compiler_continuing_clip_is_compact():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball")])]), w, 1)
    beat = _beat(clips=[
        _clip(action="He kicks.", end_state="mid-kick", on_screen=["lego_man", "soccer_ball"]),
        _clip(action="He watches the ball roll.", end_state="watching", on_screen=["lego_man", "soccer_ball"], camera_motion="slow push in"),
    ])
    style = "Studio product shot. Pure white cyclorama."
    p1 = compile_clip(beat, 0, w, picture=Picture(style=style, anchor="Studio shot, white."), segment_no=2)
    assert p1.startswith("Studio shot, white.") and "Pure white cyclorama" not in p1, "the anchor replaces the style paragraph"
    assert "Lego Man (the same plain LEGO minifigure" in p1 and "the most default" not in p1, "short look on continues"
    assert "soccer ball;" in p1 or "soccer ball." in p1
    assert "(a studded LEGO ball)" not in p1, "a prop already introduced is named, not described"
    assert "The shot opens with" not in p1 and "Cut to" not in p1
    p2 = compile_clip(beat, 1, w, picture=Picture(style=style), segment_no=3)
    assert p2.startswith("Studio product shot.") and "cyclorama" not in p2, "no anchor: the style's first sentence"
    assert "Medium shot; the camera makes a slow push in." in p2
    # The clip that introduces a prop carries its description, as an entrance
    # rather than as something already on screen (LTX-03).
    beat3 = _beat(clips=[_clip(action="A cone appears.", end_state="cone", on_screen=["lego_man", "cone"],
                               entity_ops=[EntityOp(op="introduce", entity="cone", description="a yellow LEGO cone")])])
    _apply(beat3, w, 4)
    p3 = compile_clip(beat3, 0, w, picture=Picture(style=style), segment_no=4)
    assert "Entering the picture during this clip: cone (a yellow LEGO cone). A cone appears." in p3
    assert "On screen: Lego Man (the same plain" in p3 and "; cone" not in p3, "an entering entity is not listed as present"


def test_pacing_in_story_seconds():
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
    assert "…" in text, "long status strings are clipped"
    assert "looks:" not in text and "xxxx" not in text, \
        "a prop's look is the compiler's business, not the Director's (CLEAN-07)"
    assert "status: sss" in text, "the status still shows"
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
    prompt = compile_clip(beat, 0, w, picture=PLAIN, segment_no=1)
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
        # A world.json written before CLEAN-03 carries `reference_image` on
        # every entity; it still loads (unknown keys are ignored).
        import json
        raw = json.loads((Path(d) / "world.json").read_text())
        raw["entities"]["lego_man"]["reference_image"] = "data/legoman.png"
        (Path(d) / "world.json").write_text(json.dumps(raw))
        old = WorldModel.load(d)
        assert "lego_man" in old.entities and not hasattr(old.entities["lego_man"], "reference_image")


def test_the_reference_belongs_to_the_picture():
    """The reference image and what it shows live on the picture, not on a
    character; the image resolves against the story folder; a story may
    have none (the renderer refuses a video run without one)."""
    import json
    raw = json.loads(Path("stories/lego-void/story.json").read_text())
    image = raw["picture"]["reference"]["image"]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "story.json").write_text(json.dumps(raw))
        (Path(d) / image).write_bytes((Path("stories/lego-void") / image).read_bytes())
        story = Story.from_json(d)
    assert story.reference_image == str(Path(d) / image), "the image resolves against the story folder"
    assert story.picture.reference.shows.startswith("Lego Man standing upright")
    raw["picture"].pop("reference")
    bare = Story(**raw)
    assert bare.reference_image == "" and WorldModel.from_story(bare).opening_picture is None


def test_location_resolver_forms_and_prefix_collision():
    """CLEAN-09: no multi-location story has run. The resolver takes ids,
    names and prefixed forms; a decorated reference falls through to the
    substring fallback, which takes the first location id contained in the
    text, so a location whose id is a prefix of another's wins there (the
    known gap from review 01 §4.4, pinned here until a two-location story
    needs it fixed)."""
    from src.models.story import Location
    from src.story.validator import resolve_location
    story = Story.from_json("stories/lego-void")
    locations = [Location(name="The Void", description="white"), Location(name="The Void Annex", description="whiter")]
    w = WorldModel.from_blueprint(title=story.title, characters=story.cast.characters, locations=locations)
    assert [e.id for e in w.by_kind("location")] == ["the_void", "the_void_annex"]
    assert w.current_location().id == "the_void", "the first location is home"
    # Exact ids, names, slugs and prefixed forms all resolve to the right one.
    for ref in ("the_void_annex", "The Void Annex", "the void annex", "at the_void_annex", "in The Void Annex", "inside the void annex"):
        assert resolve_location(w, ref).id == "the_void_annex", ref
    for ref in ("the_void", "The Void", "at the void", "on the_void"):
        assert resolve_location(w, ref).id == "the_void", ref
    assert resolve_location(w, "the skateboard") is None, "no location id in the text: unresolved"
    # The known gap: a decorated reference to the annex reaches the fallback
    # and `the_void` is found inside it first.
    assert resolve_location(w, "The Void Annex, far end").id == "the_void", "the prefix collision (01 §4.4)"
    assert resolve_location(w, "far end of the annex") is None, "no location id in the text: unresolved"
    # Two locations with the same name get `_2`, and the same collision applies.
    w2 = WorldModel.from_blueprint(title=story.title, characters=story.cast.characters,
                                   locations=[Location(name="Void", description="a"), Location(name="Void", description="b")])
    assert [e.id for e in w2.by_kind("location")] == ["void", "void_2"]
    assert resolve_location(w2, "void_2").id == "void_2" and resolve_location(w2, "the void_2").id == "void_2"
    assert resolve_location(w2, "void 2, by the edge").id == "void", "the prefix collision (`void` inside `void_2`)"
    # A protagonist move to the annex lands through the validator and flips presence.
    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="lego_man", kind="character", location="in The Void Annex")])])
    r = _apply(beat, w, 2)
    assert not r.violations and w.entities["lego_man"].location == "the_void_annex"
    assert w.current_location().id == "the_void_annex"


def test_compiler_camera_clause():
    """CLEAN-08: the camera line is a clause whatever form the Director's
    `camera_motion` takes (session A had four ungrammatical "… camera" lines)."""
    from src.story.compiler import camera_clause
    assert camera_clause("static") == "the camera remains static"
    assert camera_clause("") == "the camera remains static" and camera_clause("Static camera.") == "the camera remains static"
    assert camera_clause("none") == "the camera remains static"
    # Today's noun phrases (session A).
    assert camera_clause("slow push-in toward the pair") == "the camera makes a slow push-in toward the pair"
    assert camera_clause("slight push-in for comedic effect") == "the camera makes a slight push-in for comedic effect"
    assert camera_clause("slow pan to follow Lego Man's movement") == "the camera makes a slow pan to follow Lego Man's movement"
    assert camera_clause("orbit around him") == "the camera makes an orbit around him"
    # Verb phrases (the form LTX-04 will ask the Director for) and participles.
    assert camera_clause("slowly pushes in") == "the camera slowly pushes in"
    assert camera_clause("pans left to follow him") == "the camera pans left to follow him"
    assert camera_clause("tracking Lego Man as he walks") == "the camera is tracking Lego Man as he walks"
    assert camera_clause("the camera slowly pushes in.") == "the camera slowly pushes in", "no double 'the camera'"
    w = _world()
    beat = _beat(framing="wide shot, low angle", clips=[_clip(camera_motion="slow push-in on the crown")])
    prompt = compile_clip(beat, 0, w, picture=PLAIN, segment_no=2)
    assert "Wide shot, low angle; the camera makes a slow push-in on the crown." in prompt
    assert " camera." not in prompt


def _emit(beat: BeatPlan, w: WorldModel, seg: int, index: int = 0, **kw) -> str:
    """Apply, compile and record the clip the way the engine emits it."""
    _apply(beat, w, seg, index)
    prompt = compile_clip(beat, index, w, picture=PLAIN, segment_no=seg, **kw)
    clip = beat.clips[index]
    w.events.append(EventRecord(segment=seg, scene_id=1, narration=clip.action_text(),
                                on_screen=list(clip.on_screen), end_state=clip.end_state, duration=6))
    return prompt


def test_compiler_describes_entering_and_returning_entities():
    """LTX-03: an entity is described on the clip in which it enters the
    picture and is never listed as already on screen; a prop introduced
    by an op the Director left out of `on_screen` (session A, the crown)
    is described too; a returning dormant prop gets its identifiers again;
    the clip after an entrance lists the entity normally."""
    w = _world()
    p1 = _emit(_beat(clips=[_clip(action="He looks around.", on_screen=["lego_man"])]), w, 1, opening=True)
    assert "On screen: Lego Man (the most default" in p1 and "Entering" not in p1
    # Introduced and listed: an entrance, not a presence.
    ball = _beat(clips=[_clip(action="A soccer ball rolls in and stops at his feet.", on_screen=["lego_man", "soccer_ball"],
                              entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball")])])
    p2 = _emit(ball, w, 2)
    assert "On screen: Lego Man (the same plain LEGO minifigure" in p2 and "soccer ball" not in p2.split("Entering")[0]
    assert "Entering the picture during this clip: soccer ball (a studded LEGO ball). A soccer ball rolls in" in p2
    # The next clip: the ball was in the previous picture, so it is listed by name.
    p3 = _emit(_beat(clips=[_clip(action="He nudges the ball.", on_screen=["lego_man", "soccer_ball"])]), w, 3)
    assert "On screen: Lego Man (the same plain LEGO minifigure: bare yellow head with two-dot eyes and a curved smile, plain red torso, plain blue legs, claw hands); soccer ball." in p3
    assert "Entering" not in p3 and "(a studded LEGO ball)" not in p3
    # Introduced by an op but not listed (the crown at segment 53): described anyway.
    crown = _beat(clips=[_clip(action="The crown pops off with a click. He holds it up.", on_screen=["lego_man"],
                               entity_ops=[EntityOp(op="introduce", entity="princess crown", description="a bright, blocky LEGO crown", holder="lego_man")])])
    p4 = _emit(crown, w, 4)
    assert "Entering the picture during this clip: princess crown (a bright, blocky LEGO crown). The crown pops off" in p4
    assert "On screen: Lego Man (the same plain" in p4 and "soccer ball" not in p4, "not listed this clip: not in the picture"
    # Listed for the first time on the clip after its entrance: it was in the picture, so no second entrance.
    p5 = _emit(_beat(clips=[_clip(action="He turns the crown over.", on_screen=["lego_man", "princess_crown"])]), w, 5)
    assert "; princess crown." in p5 and "Entering" not in p5
    # The ball, absent from two clips' pictures, returns: identifiers again.
    p6 = _emit(_beat(clips=[_clip(action="He kicks the ball, crown still in hand.", on_screen=["lego_man", "princess_crown", "soccer_ball"])]), w, 6)
    assert "Entering the picture during this clip: soccer ball (a studded LEGO ball). He kicks the ball" in p6
    assert "On screen: Lego Man (the same plain LEGO minifigure: bare yellow head with two-dot eyes and a curved smile, plain red torso, plain blue legs, claw hands); princess crown." in p6
    # A character that leaves the frame and comes back is re-identified in full.
    p7 = _emit(_beat(clips=[_clip(action="The ball sits alone; he is out of frame.", end_state="the ball alone", on_screen=["soccer_ball"])]), w, 7)
    assert "On screen: soccer ball." in p7 and "Lego Man" not in p7
    p8 = _emit(_beat(clips=[_clip(action="He walks back in carrying a hat.", on_screen=["lego_man", "soccer_ball", "hat"],
                               entity_ops=[EntityOp(op="introduce", entity="hat", description="a red LEGO cap")])]), w, 8)
    assert "Entering the picture during this clip: Lego Man (the most default LEGO minifigure" in p8, "characters first, full look"
    assert "; hat (a red LEGO cap). He walks back in" in p8 and "On screen: soccer ball." in p8
    # A look the Director's action already carries verbatim is not repeated.
    dup = _beat(clips=[_clip(action="A brick-built dog, a squat grey LEGO dog with a stud nose, trots in.", on_screen=["lego_man", "soccer_ball", "hat", "dog"],
                             entity_ops=[EntityOp(op="introduce", entity="dog", description="a squat grey LEGO dog with a stud nose")])])
    p9 = _emit(dup, w, 9)
    assert "Entering the picture during this clip: dog. A brick-built dog" in p9 and p9.count("stud nose") == 1
    # An entity introduced off screen (present=False) does not enter.
    off = _beat(clips=[_clip(action="A bark from somewhere off screen.", on_screen=["lego_man", "dog"],
                             entity_ops=[EntityOp(op="introduce", entity="unseen cat", description="a LEGO cat", present=False)])])
    p10 = _emit(off, w, 10)
    assert "cat" not in p10 and "Entering" not in p10
    # No previous record (a replay with a gap, or segment 0): nothing is called a return.
    w2 = _world()
    p = compile_clip(_beat(clips=[_clip(on_screen=["lego_man"])]), 0, w2, picture=PLAIN, segment_no=5)
    assert "On screen: Lego Man (" in p and "Entering" not in p


def test_a_deferred_request_is_resolved_by_identity():
    """ARCH-07: with two requests owed, the beat names the one it delivers
    by its segment; an absorbed beat that names none resolves neither
    (it used to take the last one); an unknown segment is ignored."""
    w = _world()
    for seg, text in ((2, "kick the ball"), (5, "wear a hat")):
        w.add_intervention(seg, text)
        _apply(_beat(intervention="deferred", intervention_reason="later"), w, seg)
    assert [i.disposition for i in w.interventions] == ["deferred", "deferred"]

    r = _apply(_beat(intervention="absorbed"), w, 6)
    assert [i.disposition for i in w.interventions] == ["deferred", "deferred"]
    assert any("2 requests owed and no delivered_request" in x for x in r.warnings), r.warnings

    beat = _beat(delivered_request=9)
    assert any("names no deferred request" in x for x in check(beat, w).warnings)
    r = _apply(beat, w, 7)
    assert [i.disposition for i in w.interventions] == ["deferred", "deferred"] and r.warnings

    r = _apply(_beat(delivered_request=2, intervention_reason="the kick, at last"), w, 8)
    kick, hat = w.interventions
    assert (kick.disposition, kick.resolved_at, kick.reason) == ("absorbed", 8, "the kick, at last")
    assert hat.disposition == "deferred", "the other request is still owed"
    assert any("(segment 2) now absorbed" in a for a in r.applied)

    w.add_intervention(9, "jump")            # a new line answered while an old one is delivered
    _apply(_beat(intervention="absorbed", intervention_reason="he jumps", delivered_request=5), w, 9)
    assert [(i.text, i.disposition, i.resolved_at) for i in w.interventions] == [
        ("kick the ball", "absorbed", 8), ("wear a hat", "absorbed", 9), ("jump", "absorbed", 9)]
    assert "delivered_request" in BeatPlan.model_fields, "a strict-subset int field"


def test_an_answer_beat_is_capped_and_told_so():
    """ARCH-11: a beat that answers a viewer line has at most
    `answer_max_clips` clips; the cap is in the user prompt's gates (the
    system prompt stays byte-identical) and the engine enforces it."""
    from src.agents.director import clip_cap
    from src.story.context import format_gates
    cfg = _config()
    assert cfg.answer_max_clips == 2
    assert clip_cap(cfg, "a ball appears") == 2 and clip_cap(cfg, "  ") == cfg.max_clips_per_beat == 4
    assert clip_cap(_config(answer_max_clips=6), "x") == 4, "never above the beat cap"
    gates = format_gates(pacing="p", scene="s", answer_clips=2)
    assert gates.endswith("This beat answers the viewer: at most 2 clips. Start the answer now; the next beat continues it.")
    assert "answers the viewer" not in format_gates(pacing="p", scene="s")
    beat = _beat(clips=[_clip(), _clip(), _clip()])
    assert any("only the first 2" in x for x in check(beat, _world(), max_clips=clip_cap(cfg, "x")).warnings)

