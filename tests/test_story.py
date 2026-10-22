"""The deterministic story layer, no LLM: the world model built from a
blueprint, the validator (the only code that mutates the world), the
prompt compiler and persistence."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.models.config import Config
from src.models.story import Cast, Character, Location, Picture, Story, Voice
from src.story.compiler import compile_clip, speech
from src.story.context import format_cut_rule, format_plan, format_world_now
from src.story.schemas import BeatPlan, ClipSpec, DialogueLine, EntityOp, SceneCardOut, ShowrunnerUpdate, ThreadOp
from src.story.validator import apply_clip, apply_showrunner, check, resolve_location
from src.story.world import EventRecord, WorldModel

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


def _beat(*ops: EntityOp, **over) -> BeatPlan:
    base = dict(intent="a wave", framing="medium shot", clips=[_clip(entity_ops=list(ops))] if ops else [_clip()])
    base.update(over)
    return BeatPlan(**base)


def _apply(beat: BeatPlan, w: WorldModel, segment_no: int, index: int = 0):
    return apply_clip(beat, index, w, segment_no=segment_no)


def _emit(beat: BeatPlan, w: WorldModel, seg: int, index: int = 0, **kw) -> str:
    """Apply, compile and record the clip the way the engine emits it."""
    _apply(beat, w, seg, index)
    prompt = compile_clip(beat, index, w, picture=PLAIN, segment_no=seg, **kw)
    clip = beat.clips[index]
    here = w.current_location()
    w.events.append(EventRecord(segment=seg, scene_id=1, narration=clip.action_text(),
                                on_screen=list(clip.on_screen), end_state=clip.end_state,
                                location=here.id if here else "", duration=6))
    return prompt


def _road(**picture) -> WorldModel:
    """A story with three places, two of them with looks, and a squire
    waiting in the forest."""
    story = Story(
        title="The road", premise="A knight sets out.",
        locations=[Location(name="Castle", description="home", look="a grey castle", look_short="the castle"),
                   Location(name="Castle Tower", description="the top", look="a tall tower"),
                   Location(name="Forest", description="the wild", look="tall pine trees", look_short="the pines")],
        cast=Cast(characters=[Character(name="Knight", look="a knight"),
                              Character(name="Squire", look="a boy", location="Forest")]),
        picture=Picture(**picture),
    )
    return WorldModel.from_story(story)


def _go(where: str, **clip) -> BeatPlan:
    """A beat whose one clip takes the knight to `where`."""
    ops = [EntityOp(op="update", entity="knight", kind="character", location=where), *clip.pop("entity_ops", [])]
    return _beat(clips=[_clip(**{"on_screen": ["knight"], **clip, "entity_ops": ops})])


def test_world_from_blueprint():
    w = _world()
    assert w.protagonist == "lego_man"
    assert not w.cast_locked and w.newcomers.startswith("Other minifigures from any LEGO theme")
    hero, spaceman = w.entities["lego_man"], w.entities["spaceman"]
    assert hero.present is True and hero.location == "the_white_void"
    assert hero.description.startswith("The most default minifigure") and hero.wants == "Something to do, and to get it right."
    assert hero.voice.description.startswith("earnest") and hero.state == "standing upright centre frame, arms at his sides"
    assert hero.look_short.startswith("the same plain LEGO minifigure"), "the blueprint's short look carries over"
    assert not spaceman.present and spaceman.appears == "must" and spaceman.location == ""
    assert w.opening_picture == ["lego_man"], "the reference image shows Lego Man, not the Spaceman"
    assert w.current_location().id == "the_white_void"
    assert w.resolve("Lego Man") is hero and w.resolve("lego man") is hero


def test_validator_introduce_update_remove_and_character_state():
    w = _world()
    beat = _beat(clips=[_clip(on_screen=["lego_man", "soccer ball"], entity_ops=[
        EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball", state="at his feet"),
        EntityOp(op="update", entity="lego_man", kind="character", state="crouching centre frame, eyeing the ball"),
    ])])
    r = _apply(beat, w, 2)
    assert not r.violations
    ball = w.entities["soccer_ball"]
    assert ball.present and ball.location == "the_white_void" and ball.first_seen == 2
    assert beat.clips[0].on_screen == ["lego_man", "soccer_ball"], "references normalised to ids"
    hero = w.entities["lego_man"]
    assert ball.state == "at his feet" and hero.state == "crouching centre frame, eyeing the ball"

    # Who holds a prop is its state, in prose, replaced whole.
    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="soccer_ball", state="in Lego Man's hands"),
                                          EntityOp(op="update", entity="lego_man", kind="character", state="standing")])])
    r = _apply(beat, w, 3)
    assert ball.state == "in Lego Man's hands" and hero.state == "standing" and not r.warnings
    assert r.applied == ["update soccer_ball: state=in Lego Man's hands", "update lego_man: state=standing"]

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="soccer_ball", state="dropped", present=False)])])
    _apply(beat, w, 5)
    assert ball.state == "dropped" and not ball.present and ball in w.dormant_props()

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="remove", entity="soccer_ball", state="disassembled")])])
    _apply(beat, w, 6)
    assert ball.gone and ball.state == "disassembled" and ball not in w.dormant_props() and ball in w.gone_props()

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="update", entity="ghost", state="x")])])
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
        _clip(entity_ops=[EntityOp(op="update", entity="hat", state="on Lego Man's head")]),  # introduced in clip 1: fine
        _clip(entity_ops=[EntityOp(op="update", entity="nothing", state="x")]),
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


def test_the_validator_protects_the_protagonist_and_the_policy():
    """A newcomer without a look, a second Lego Man and the protagonist's
    removal are violations and are not applied; the Spaceman introduced by
    name is brought in, not duplicated."""
    w = _world()
    faceless = EntityOp(op="introduce", entity="chef", kind="character")
    double = EntityOp(op="introduce", entity="Lego Man 2", kind="character", description="another plain figure")
    gone = EntityOp(op="remove", entity="lego_man")
    r = check(_beat(faceless, double, gone), w)
    words = " ".join(r.violations)
    assert "needs a `description`" in words and "never appears twice" in words and "cannot be removed" in words
    apply_clip(_beat(faceless, double, gone), 0, w, segment_no=3)
    assert "chef" not in w.entities and "lego_man_2" not in w.entities and not w.entities["lego_man"].gone
    spaceman = EntityOp(op="introduce", entity="Spaceman", kind="character", description="an astronaut")
    both = _beat(clips=[_clip(on_screen=["lego_man", "spaceman"], entity_ops=[spaceman])])
    r = check(both, w)
    assert not r.violations and any("already exists as spaceman; treated as update" in x for x in r.warnings)
    apply_clip(both, 0, w, segment_no=4)
    assert w.entities["spaceman"].present and "spaceman_2" not in w.entities and not w.entities["spaceman"].dynamic


def test_a_newcomer_is_registered_once_and_stays_the_same():
    """Introduced where the story allows it: registered with its look, a
    short look for later clips, who it is, its voice and its state; it may
    speak on the clip it enters; described on entering, carried by its
    short look after; listed in World now (not on the cached cast sheet)."""
    w = _world()
    from src.story.context import format_cast, format_cast_rules, format_world_now
    system_before = format_cast(w) + format_cast_rules(w)
    pirate = EntityOp(op="introduce", entity="pirate captain", kind="character",
                      description="a LEGO pirate captain minifigure in a black tricorn hat and red coat; a peg leg",
                      who="a castaway who thinks the void is a becalmed sea", voice="gravelly, booming",
                      state="standing on his peg leg at frame right, squinting at the horizon")
    hello = DialogueLine(speaker="pirate captain", line="Ahoy!")
    beat = _beat(clips=[_clip(on_screen=["lego_man", "pirate captain"], entity_ops=[pirate], dialogue=[hello])])
    r = check(beat, w)
    assert not r.violations and not r.warnings, (r.violations, r.warnings)
    apply_clip(beat, 0, w, segment_no=12)
    ent = w.entities["pirate_captain"]
    assert ent.dynamic and ent.first_seen == 12 and ent.present
    assert ent.description == "a castaway who thinks the void is a becalmed sea"
    assert ent.voice.description == "gravelly, booming" and beat.clips[0].dialogue == [hello]
    assert ent.look_short == "a LEGO pirate captain minifigure in a black tricorn hat and red coat"
    assert ent.state == "standing on his peg leg at frame right, squinting at the horizon"
    prompt = compile_clip(beat, 0, w, picture=PLAIN, segment_no=12)
    assert "Entering the picture during this clip: pirate captain (a LEGO pirate captain minifigure" in prompt
    assert 'Pirate captain says in a gravelly, booming voice, "Ahoy!"' in prompt, "the speaker opens a sentence"
    later = _beat(clips=[_clip(on_screen=["lego_man", "pirate_captain"])])
    apply_clip(later, 0, w, segment_no=13)
    prompt = compile_clip(later, 0, w, picture=PLAIN, segment_no=13)
    assert "pirate captain (a LEGO pirate captain minifigure in a black tricorn hat and red coat)" in prompt, \
        "identity restated on every later clip"
    assert format_cast(w) + format_cast_rules(w) == system_before, "the system prompt stays byte-identical (cache)"
    now = format_world_now(w)
    assert "pirate_captain (character): pirate captain" in now and "introduced at segment 12" in now
    assert "who: a castaway who thinks the void is a becalmed sea" in now and "voice: gravelly, booming" in now
    voiceless = EntityOp(op="introduce", entity="knight", kind="character", description="a LEGO knight")
    assert any("has no `voice`" in x for x in check(_beat(voiceless), w).warnings)


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


def test_cuts_are_the_storys_choice():
    """One continuous take by default: a cut is dropped and the Director
    is told `cut_to` stays empty. A story that allows cuts keeps one (not
    on the film's first clip), and only then does the Director read how to
    write it."""
    w = _world()
    assert not w.cuts and "`cut_to` stays empty" in format_cut_rule(w)
    w.events.append(EventRecord(segment=1, scene_id=1, narration="n"))
    cut = _beat(clips=[_clip(cut_to="close-up of his hands")])
    r = _apply(cut, w, 2)
    assert cut.clips[0].cut_to == "" and any("one continuous take" in x for x in r.warnings)

    story = Story.from_json("stories/lego-void")
    story = story.model_copy(update={"picture": story.picture.model_copy(update={"cuts": True,
                                                                                 "cutting": "Cut on a reveal."})})
    w = WorldModel.from_story(story)
    rule = format_cut_rule(w)
    assert w.cuts and "Write the new shot in `cut_to`" in rule and rule.endswith("Cut on a reveal.")
    assert "a cut costs the picture" in rule, "the Director knows what a cut does to the render"
    assert "arrive at another location by cutting to it" in rule, "a walk keeps drawing the old place"
    w.events.append(EventRecord(segment=1, scene_id=1, narration="n"))
    cut = _beat(clips=[_clip(cut_to="close-up of his hands")])
    _apply(cut, w, 2)
    assert cut.clips[0].cut_to == "close-up of his hands" and w.shot == "close-up of his hands"


def test_the_showrunner_lands_an_unnumbered_arc():
    """The plan numbers the acts; one the model numbered itself ("Act 2: …")
    lands without its number, so the Director does not read it twice."""
    w = _world()
    update = ShowrunnerUpdate(arc=["Act 1: He wakes.", "ACT II - He finds a ball.", "Actors arrive."], current_act=1,
                              next_scene=SceneCardOut(goal="g", tension="t", exit_condition="e"))
    apply_showrunner(w, update, at_segment=0, force_replan=True)
    assert w.plan.arc == ["He wakes.", "He finds a ball.", "Actors arrive."] and w.plan.current_act == 1
    assert "▶ act 2: He finds a ball." in format_plan(w, Config())


def test_a_location_reference_resolves_to_the_longest_whole_name():
    w = _road()
    assert resolve_location(w, "castle_tower").id == "castle_tower"
    assert resolve_location(w, "at the castle tower gate").id == "castle_tower", "not `castle`, a prefix of it"
    assert resolve_location(w, "in the castle courtyard").id == "castle"
    assert resolve_location(w, "forestry office") is None, "whole words only"


def test_the_scene_follows_the_protagonist():
    """When the knight moves, what is in the picture and what is new comes
    along; whatever else was in the scene stays behind, listed where it was
    left. A character waiting elsewhere says where."""
    w = _road()
    assert w.current_location().id == "castle" and w.entities["squire"].location == "forest"
    assert "`squire` (not yet in the film, at `forest`)" in format_world_now(w)
    lantern = EntityOp(op="introduce", entity="lantern", description="a brass lantern")
    cart = EntityOp(op="introduce", entity="cart", description="a wooden cart")
    _emit(_beat(clips=[_clip(on_screen=["knight"], entity_ops=[lantern, cart])]), w, 1)

    mapped = EntityOp(op="introduce", entity="map", description="a paper map")
    r = _apply(_go("forest", on_screen=["knight", "lantern"], entity_ops=[mapped]), w, 2)
    assert w.current_location().id == "forest"
    for prop in ("lantern", "map"):
        assert w.entities[prop].present and w.entities[prop].location == "forest", prop
    assert not w.entities["cart"].present and w.entities["cart"].location == "castle"
    assert "moved to forest; left behind at castle: cart" in r.applied
    now = format_world_now(w)
    assert "`cart` (last seen segment 1, left at `castle`)" in now and "`squire` (not yet in the film)" in now

    w2 = _road()
    _apply(_beat(clips=[_clip(on_screen=["knight"])]), w2, 1)
    r = _apply(_beat(clips=[_clip(on_screen=["knight"])]), w2, 2)
    assert not any(a.startswith("moved") for a in r.applied), "staying put moves nothing"


def test_a_place_with_a_look_is_in_the_video_prompt():
    """In full on the opening clip and on the clip that arrives there
    (after the action, or after the cut), by its short look while the
    picture stays; a place without a look (the white void) never."""
    w = _road()
    assert "Setting: Castle (a grey castle)." in _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 1, opening=True)
    assert "Setting: Castle (the castle)." in _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 2)
    walk = _emit(_go("forest", action="He walks into the trees."), w, 3)
    assert "Setting:" not in walk
    assert walk.index("He walks into the trees.") < walk.index("Arriving in Forest (tall pine trees).")
    assert "Setting: Forest (the pines)." in _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 4)

    w = _road(cuts=True)
    _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 1, opening=True)
    cut = _emit(_go("forest", cut_to="wide shot of the forest path"), w, 2)
    assert "The view cuts to a wide shot of the forest path" in cut and "Arriving" not in cut
    assert cut.index("The view cuts to") < cut.index("The new setting: Forest (tall pine trees).")

    w = _world()
    assert "Setting" not in _emit(_beat(), w, 1, opening=True) + _emit(_beat(), w, 2)


def test_the_writers_see_the_place_as_the_video_model_draws_it():
    """So a picture that has moved on from the place it is told shows."""
    assert format_world_now(_road()).startswith("Location: `castle` Castle · drawn as: the castle\n")
    w = _road()
    _apply(_go("castle_tower"), w, 1)
    assert format_world_now(w).startswith("Location: `castle_tower` Castle Tower · drawn as: a tall tower\n"), \
        "no short look: the full one"
    assert format_world_now(_world()).startswith("Location: `the_white_void` The White Void\n"), "no look, nothing said"


def test_a_newcomers_short_look_ends_on_a_whole_phrase():
    w = _world()
    look = "A small LEGO forest traveler with a dark green hood, brown torso and a handcart wheel beside them."
    traveler = EntityOp(op="introduce", entity="forest traveler", kind="character", description=look, voice="dry")
    _apply(_beat(clips=[_clip(on_screen=["lego_man", "forest traveler"], entity_ops=[traveler])]), w, 2)
    assert w.entities["forest_traveler"].look_short == "A small LEGO forest traveler with a dark green hood"
    # A prop gets one too, and a look without a comma still ends on a whole phrase.
    look = "A round red LEGO shield with a raised golden lion emblem and a black rim and handle"
    shield = EntityOp(op="introduce", entity="lion shield", kind="prop", description=look)
    _apply(_beat(clips=[_clip(on_screen=["lego_man", "lion shield"], entity_ops=[shield])]), w, 3)
    assert w.entities["lion_shield"].look_short == "A round red LEGO shield with a raised golden lion emblem"


def test_a_line_says_each_quality_of_the_voice_once():
    voice = Voice(description="earnest, slightly formal, a little breathless", language="English")
    assert speech("Lego Man", "Onward.", delivery="formal, slightly breathless", voice=voice) == \
        'Lego Man says in a formal, slightly breathless, earnest voice, in English, "Onward."'
    assert speech("Lego Man", "Hi.", delivery="quiet", voice=voice).startswith(
        "Lego Man says in a quiet, earnest, slightly formal, a little breathless voice")


def test_a_character_starts_only_in_a_known_place():
    with pytest.raises(ValueError, match="not one of the locations"):
        Story(title="t", premise="p", locations=[Location(name="Here", description="d")],
              cast=Cast(characters=[Character(name="A"), Character(name="B", location="There")]))
    quest = WorldModel.from_story(Story.from_json("stories/lego-quest"))
    assert quest.cuts and quest.current_location().id == "the_village"
    assert quest.entities["princess"].location == quest.entities["dragon"].location == "the_dragons_tower"


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
    assert 'Lego Man says in an earnest, slightly formal, a little breathless voice, in English, "Hello?"' in prompt
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
    assert "soccer ball (a studded LEGO ball)" in p1, "a prop already introduced carries its short look"
    assert "The shot opens with" not in p1 and "Cut to" not in p1
    p2 = compile_clip(beat, 1, w, picture=Picture(style=style), segment_no=3)
    assert p2.startswith("Studio product shot.") and "cyclorama" not in p2, "no anchor: the style's first sentence"
    assert "Medium shot; the camera makes a slow push in." in p2
    # The clip that introduces a prop carries its description, as an entrance
    # rather than as something already on screen.
    beat3 = _beat(clips=[_clip(action="A cone appears.", end_state="cone", on_screen=["lego_man", "cone"],
                               entity_ops=[EntityOp(op="introduce", entity="cone", description="a yellow LEGO cone")])])
    _apply(beat3, w, 4)
    p3 = compile_clip(beat3, 0, w, picture=Picture(style=style), segment_no=4)
    assert "Entering the picture during this clip: cone (a yellow LEGO cone). A cone appears." in p3
    assert "On screen: Lego Man (the same plain" in p3 and "; cone" not in p3, "an entering entity is not listed as present"


def test_compiler_describes_entering_and_returning_entities():
    """An entity is described on the clip in which it enters the picture
    and is never listed as already on screen; a prop introduced by an op
    the Director left out of `on_screen` is described too; a returning dormant prop gets its identifiers again;
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
    # The next clip: the ball was in the previous picture, so it is listed with its short look.
    p3 = _emit(_beat(clips=[_clip(action="He nudges the ball.", on_screen=["lego_man", "soccer_ball"])]), w, 3)
    assert "On screen: Lego Man (the same plain LEGO minifigure: bare yellow head with two-dot eyes and a curved smile, plain red torso, plain blue legs, claw hands); soccer ball (a studded LEGO ball)." in p3
    assert "Entering" not in p3
    # Introduced by an op but not listed: described anyway.
    crown = _beat(clips=[_clip(action="The crown pops off with a click. He holds it up.", on_screen=["lego_man"],
                               entity_ops=[EntityOp(op="introduce", entity="princess crown", description="a bright, blocky LEGO crown", state="in Lego Man's hand")])])
    p4 = _emit(crown, w, 4)
    assert "Entering the picture during this clip: princess crown (a bright, blocky LEGO crown). The crown pops off" in p4
    assert "On screen: Lego Man (the same plain" in p4 and "soccer ball" not in p4, "not listed this clip: not in the picture"
    # Listed for the first time on the clip after its entrance: it was in the picture, so no second entrance.
    p5 = _emit(_beat(clips=[_clip(action="He turns the crown over.", on_screen=["lego_man", "princess_crown"])]), w, 5)
    assert "; princess crown (a bright, blocky LEGO crown)." in p5 and "Entering" not in p5
    # The ball, absent from two clips' pictures, returns: identifiers again.
    p6 = _emit(_beat(clips=[_clip(action="He kicks the ball, crown still in hand.", on_screen=["lego_man", "princess_crown", "soccer_ball"])]), w, 6)
    assert "Entering the picture during this clip: soccer ball (a studded LEGO ball). He kicks the ball" in p6
    assert "On screen: Lego Man (the same plain LEGO minifigure: bare yellow head with two-dot eyes and a curved smile, plain red torso, plain blue legs, claw hands); princess crown (a bright, blocky LEGO crown)." in p6
    # A character that leaves the frame and comes back is re-identified in full.
    p7 = _emit(_beat(clips=[_clip(action="The ball sits alone; he is out of frame.", end_state="the ball alone", on_screen=["soccer_ball"])]), w, 7)
    assert "On screen: soccer ball (a studded LEGO ball)." in p7 and "Lego Man" not in p7
    p8 = _emit(_beat(clips=[_clip(action="He walks back in carrying a hat.", on_screen=["lego_man", "soccer_ball", "hat"],
                               entity_ops=[EntityOp(op="introduce", entity="hat", description="a red LEGO cap")])]), w, 8)
    assert "Entering the picture during this clip: Lego Man (the most default LEGO minifigure" in p8, "characters first, full look"
    assert "; hat (a red LEGO cap). He walks back in" in p8 and "On screen: soccer ball (a studded LEGO ball)." in p8
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


def test_persistence_round_trip():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="hat", description="a red cap")])]), w, 1)
    w.events.append(EventRecord(segment=1, scene_id=1, narration="n", beat=1, beat_intent="a hat"))
    with tempfile.TemporaryDirectory() as d:
        w.save(d)
        w.save(d)  # second save must not duplicate events
        loaded = WorldModel.load(d)
        assert "hat" in loaded.entities and len(loaded.events) == 1 and loaded.events[0].beat_intent == "a hat"
        assert (Path(d) / "events.jsonl").read_text().count("\n") == 1


def test_every_blueprint_field_is_in_the_field_guide():
    """`stories/README.md` says what each field of `story.json` is and does;
    a field added to the blueprint without a line there fails here."""
    import typing
    from pydantic import BaseModel
    guide = Path("stories/README.md").read_text()

    def models(annotation) -> list[type[BaseModel]]:
        args = typing.get_args(annotation) or (annotation,)
        return [a for arg in args for a in ([arg] if isinstance(arg, type) and issubclass(arg, BaseModel)
                                            else models(arg) if typing.get_args(arg) else [])]

    seen, todo, missing = set(), [Story], []
    while todo:
        model = todo.pop()
        if model in seen:
            continue
        seen.add(model)
        for name, field in model.model_fields.items():
            if f"`{name}`" not in guide:
                missing.append(f"{model.__name__}.{name}")
            todo.extend(models(field.annotation))
    assert not missing, f"document these in stories/README.md: {missing}"
    assert {m.__name__ for m in seen} >= {"Story", "Storyteller", "Cast", "Character", "Voice", "Picture", "Reference",
                                          "Location"}
