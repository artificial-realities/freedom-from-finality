"""The deterministic story layer, no LLM: the world model built from a
frame zero, the validator (the only code that mutates the world), the
prompt compiler and persistence."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.models.story import FrameZero, StartingCharacter, StartingLocation, Voice
from src.story.compiler import compile_clip, speech
from src.story.context import format_world_now
from src.story.schemas import BeatPlan, CharacterPortrait, ClipSpec, DialogueLine, EntityOp, ThenUpdate, Notebook, Seed
from src.story.validator import apply_clip, apply_then, check, resolve_location
from src.story.world import Entity, EventRecord, WorldModel

# Lego Man's short look, made from his look the way a newcomer's is.
LEGO_SHORT = "the most default LEGO minifigure: bare yellow head with no hair piece"


def _world(**kw) -> WorldModel:
    fz = FrameZero.from_json("tests/fixtures/lego-void")
    if kw:
        fz = fz.model_copy(update=kw)
    return WorldModel.from_frame_zero(fz)


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
    _apply(beat, w, seg, index)
    prompt = compile_clip(beat, index, w, segment_no=seg, **kw)
    clip = beat.clips[index]
    here = w.current_location()
    w.events.append(EventRecord(segment=seg, narration=clip.action_text(),
                                on_screen=list(clip.on_screen), end_state=clip.end_state,
                                location=here.id if here else "", duration=6))
    return prompt


def _road() -> WorldModel:
    """A knight at a castle, with places and a squire the story has made since."""
    fz = FrameZero(scene="A knight at the castle gate.", character=StartingCharacter(name="Knight", look="a knight"),
                   location=StartingLocation(name="Castle", look="a grey castle"))
    w = WorldModel.from_frame_zero(fz)
    w.entities["castle"].look_short = "the castle"
    w.entities["castle_tower"] = Entity(id="castle_tower", name="Castle Tower", kind="location", look="a tall tower")
    w.entities["forest"] = Entity(id="forest", name="Forest", kind="location", look="tall pine trees",
                                  look_short="the pines")
    w.entities["squire"] = Entity(id="squire", name="Squire", kind="character", look="a boy", location="forest")
    return w


def _go(where: str, **clip) -> BeatPlan:
    ops = [EntityOp(op="update", entity="knight", kind="character", location=where), *clip.pop("entity_ops", [])]
    return _beat(clips=[_clip(**{"on_screen": ["knight"], **clip, "entity_ops": ops})])


def test_world_from_frame_zero():
    w = _world()
    assert set(w.entities) == {"the_white_void", "lego_man"} and w.camera_location == "the_white_void"
    place, hero = w.entities["the_white_void"], w.entities["lego_man"]
    assert place.kind == "location" and place.look_short == place.look.rstrip(".")
    assert place.look.startswith("an infinite, featureless white space"), "the place is drawn from its look"
    assert hero.present is True and hero.location == "the_white_void" and hero.voice is None
    assert hero.state == "standing upright centre frame, arms at his sides"
    assert hero.description == "Earnest and game for anything; takes small things very seriously.", "who he is, to start"
    assert hero.look_short == LEGO_SHORT, "a short look made the way a newcomer's is"
    assert w.opening_picture == ["lego_man"], "the first frame shows the character"
    assert w.resolve("Lego Man") is hero and w.resolve("lego man") is hero


def test_a_frame_zero_is_the_first_frame_and_nothing_else():
    fz = FrameZero.from_json(".")
    assert fz.scene and fz.character.look and fz.location.look, "the project's own frame zero loads"
    base = {"scene": "s", "character": {"name": "A", "look": "a"}, "location": {"name": "Here", "look": "h"}}
    assert FrameZero(**base).reference_image == "", "only a video run needs the image"
    for stale in ({"name": "old"}, {"truths": ["x"]}, {"picture": {"style": "s"}}):
        with pytest.raises(ValueError):
            FrameZero(**base, **stale)
    with pytest.raises(ValueError):
        FrameZero(**{**base, "character": {"name": "A", "look": "a", "wants": "x"}})
    with pytest.raises(ValueError):
        FrameZero(**{**base, "location": {"name": "Here"}})


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
        _clip(entity_ops=[EntityOp(op="update", entity="hat", state="on Lego Man's head")]),
        _clip(entity_ops=[EntityOp(op="update", entity="nothing", state="x")]),
    ])
    r = check(beat, w, max_clips=4)
    assert len(r.violations) == 1 and r.violations[0].startswith("clip 3")
    r = check(beat, w, max_clips=2)
    assert not r.violations and any("only the first 2" in x for x in r.warnings), "clips past the cap are not checked"


def test_validator_speakers():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="plastic dog", description="a plastic dog")])]), w, 1)
    beat = _beat(clips=[_clip(
        entity_ops=[EntityOp(op="introduce", entity="Lego Woman", kind="character")],
        dialogue=[DialogueLine(speaker="lego_man", line="Hi"), DialogueLine(speaker="plastic_dog", line="Woof, said the dog")],
    )])
    r = check(beat, w)
    assert any("new character 'Lego Woman' needs a `description`" in v for v in r.violations)
    assert any("dialogue speaker 'plastic_dog' is not a known character id" in v for v in r.violations)
    assert not any("speaker 'lego_man'" in v for v in r.violations), "every character may speak"
    _apply(beat, w, 2)
    assert "lego_woman" not in w.entities, "a character without a look is never created"
    assert [d.speaker for d in beat.clips[0].dialogue] == ["lego_man"], "the prop's line is dropped on apply"

    beat = _beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="plastic cat", kind="prop", description="a small plastic cat")])])
    r = _apply(beat, w, 3)
    assert not r.violations and "plastic_cat" in w.entities, "creatures are props and allowed"


def test_a_newcomer_is_registered_once_and_stays_the_same():
    w = _world()
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
    prompt = compile_clip(beat, 0, w, segment_no=12)
    assert "Entering the picture during this clip: pirate captain (a LEGO pirate captain minifigure" in prompt
    assert 'Pirate captain says in a gravelly, booming voice, "Ahoy!"' in prompt, "the speaker opens a sentence"
    later = _beat(clips=[_clip(on_screen=["lego_man", "pirate_captain"])])
    apply_clip(later, 0, w, segment_no=13)
    prompt = compile_clip(later, 0, w, segment_no=13)
    assert "pirate captain (a LEGO pirate captain minifigure in a black tricorn hat and red coat)" in prompt, \
        "identity restated on every later clip"
    now = format_world_now(w)
    assert "pirate_captain (character): pirate captain" in now and "introduced at segment 12" in now
    assert "looks: a LEGO pirate captain minifigure in a black tricorn hat and red coat; a peg leg" in now
    assert "who: a castaway who thinks the void is a becalmed sea" in now and "voice: gravelly, booming" in now
    voiceless = EntityOp(op="introduce", entity="knight", kind="character", description="a LEGO knight")
    assert any("has no `voice`" in x for x in check(_beat(voiceless), w).warnings)


def test_who_a_character_is_moves_to_thens_portrait():
    w = _world()
    now = format_world_now(w)
    assert "lego_man (character): Lego Man · now: standing upright centre frame, arms at his sides · looks: the most default" in now
    assert "who: Earnest and game for anything" in now, "the starting personality, until Then has a portrait"
    portrait = CharacterPortrait(entity="lego_man", portrait="Braver now; the void no longer frightens him.")
    apply_then(w, ThenUpdate(notebook=Notebook(characters=[portrait])), at_segment=3)
    assert "who:" not in format_world_now(w), "the portrait is who he is now"


def test_a_voice_can_come_later():
    w = _world()
    assert w.entities["lego_man"].voice is None
    speak = EntityOp(op="update", entity="lego_man", kind="character", voice="earnest, a little breathless")
    hi = DialogueLine(speaker="lego_man", line="Hello?")
    beat = _beat(clips=[_clip(entity_ops=[speak], dialogue=[hi])])
    r = _apply(beat, w, 2)
    assert w.entities["lego_man"].voice.description == "earnest, a little breathless", r.applied
    assert 'Lego Man says in an earnest, a little breathless voice, "Hello?"' in compile_clip(beat, 0, w, segment_no=2)
    assert "voice: earnest, a little breathless" in format_world_now(w)


def test_validator_interventions():
    w = _world()
    w.add_intervention(2, "kick the ball")
    beat = _beat(clips=[_clip(), _clip(action="He waits.")],
                 intervention="deferred", intervention_reason="needs a run-up")
    r = _apply(beat, w, 2, index=1)
    assert w.interventions[0].disposition == "pending", "clip 2 lands neither the disposition"
    r = _apply(beat, w, 2, index=0)
    item = w.interventions[0]
    assert item.disposition == "deferred" and item.resolved_at is None
    assert w.unresolved_interventions() == [item]

    beat = _beat(intervention="absorbed", intervention_reason="here it is")
    r = _apply(beat, w, 3)
    assert item.disposition == "absorbed" and item.resolved_at == 3
    assert any("deferred viewer request (segment 2) now absorbed" in a for a in r.applied), \
        "the only request owed is resolved without the field"

    w.add_intervention(4, "do a flip")
    beat = _beat()
    r = _apply(beat, w, 4)
    assert w.interventions[-1].disposition == "deferred" and any("no disposition" in x for x in r.warnings)

    assert w.drop_pending_interventions(4) == 0, "nothing pending at 4 any more"
    w.add_intervention(5, "a line the cancelled call recorded")
    assert w.drop_pending_interventions(5) == 1 and not w.unresolved_interventions()[1:]


def test_a_cut_lands_after_the_first_clip():
    w = _world()
    cut = _beat(clips=[_clip(cut_to="close-up of his hands")])
    r = _apply(cut, w, 1)
    assert cut.clips[0].cut_to == "" and any("first clip never cuts" in x for x in r.warnings)
    w.events.append(EventRecord(segment=1, narration="n"))
    cut = _beat(clips=[_clip(cut_to="close-up of his hands")])
    _apply(cut, w, 2)
    assert cut.clips[0].cut_to == "close-up of his hands" and w.shot == "close-up of his hands"


def test_then_lands_notebook_and_summaries():
    w = _world()
    nb = Notebook(alive="A lone figure.", seeds=[Seed(seed="What is out here?", potential="The void may hold anything.")],
                  intentions="Let him explore.", characters=[], audience="")
    update = ThenUpdate(scene_summary="He woke and waved.", notebook=nb)
    notes = apply_then(w, update, at_segment=5)
    assert w.notebook.alive == "A lone figure."
    assert w.notebook.seeds[0].seed == "What is out here?"
    assert any("notebook updated" in n for n in notes)
    assert w.summaries and w.summaries[0].text == "He woke and waved."
    assert w.then_runs == 1 and w.last_then == 5


def test_a_location_reference_resolves_to_the_longest_whole_name():
    w = _road()
    assert resolve_location(w, "castle_tower").id == "castle_tower"
    assert resolve_location(w, "at the castle tower gate").id == "castle_tower", "not `castle`, a prefix of it"
    assert resolve_location(w, "in the castle courtyard").id == "castle"
    assert resolve_location(w, "forestry office") is None, "whole words only"


def test_the_camera_follows_the_action():
    w = _road()
    assert w.current_location().id == "castle" and w.entities["squire"].location == "forest"
    assert "`squire` (at `forest`)" in format_world_now(w)
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
    assert "`cart` (last seen segment 1, left at `castle`)" in now and "`squire`" in now

    w2 = _road()
    _apply(_beat(clips=[_clip(on_screen=["knight"])]), w2, 1)
    r = _apply(_beat(clips=[_clip(on_screen=["knight"])]), w2, 2)
    assert not any(a.startswith("moved") for a in r.applied), "staying put moves nothing"


def test_a_place_with_a_look_is_in_the_video_prompt():
    w = _road()
    assert "Setting: Castle (a grey castle)." in _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 1, opening=True)
    assert "Setting: Castle (the castle)." in _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 2)
    walk = _emit(_go("forest", action="He walks into the trees."), w, 3)
    assert "Setting:" not in walk
    assert walk.index("He walks into the trees.") < walk.index("Arriving in Forest (tall pine trees).")
    assert "Setting: Forest (the pines)." in _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 4)

    w = _road()
    _emit(_beat(clips=[_clip(on_screen=["knight"])]), w, 1, opening=True)
    cut = _emit(_go("forest", cut_to="wide shot of the forest path"), w, 2)
    assert "The view cuts to a wide shot of the forest path" in cut and "Arriving" not in cut
    assert cut.index("The view cuts to") < cut.index("The new setting: Forest (tall pine trees).")

    w = _world()
    void = "Setting: The White Void (an infinite, featureless white space under soft, shadowless studio light)."
    assert void in _emit(_beat(), w, 1, opening=True) and void in _emit(_beat(), w, 2)


def test_a_place_the_story_makes_is_drawn_from_its_look():
    w = _world()
    _emit(_beat(), w, 1, opening=True)
    forest = EntityOp(op="introduce", entity="Pine Forest", kind="location", description="a dark pine forest at dusk")
    _emit(_beat(clips=[_clip(entity_ops=[forest])]), w, 2)
    assert w.entities["pine_forest"].look == "a dark pine forest at dusk"
    go = EntityOp(op="update", entity="lego_man", kind="character", location="pine_forest")
    cut = _emit(_beat(clips=[_clip(cut_to="wide shot of the forest", entity_ops=[go])]), w, 3)
    assert "The new setting: Pine Forest (a dark pine forest at dusk)." in cut and "White Void" not in cut
    snow = EntityOp(op="update", entity="pine_forest", kind="location", description="a pine forest under deep snow")
    _apply(_beat(clips=[_clip(entity_ops=[snow])]), w, 4)
    assert "Setting: Pine Forest (a pine forest under deep snow)." in _emit(_beat(), w, 5), "a place's look can change"


def test_the_writers_see_the_place_as_the_video_model_draws_it():
    assert format_world_now(_road()).startswith("Location: `castle` Castle · drawn as: the castle\n")
    w = _road()
    _apply(_go("castle_tower"), w, 1)
    assert format_world_now(w).startswith("Location: `castle_tower` Castle Tower · drawn as: a tall tower\n"), \
        "no short look: the full one"
    assert format_world_now(_world()).startswith(
        "Location: `the_white_void` The White Void · drawn as: an infinite, featureless white space")


def test_a_lasting_change_of_appearance_replaces_the_look():
    w = _world()
    helmeted = "the plain LEGO minifigure wearing a silver knight's helmet with a red plume, plain red torso, plain blue legs"
    on = EntityOp(op="update", entity="lego_man", kind="character", description=helmeted, state="helmet on, centre frame")
    r = _apply(_beat(on, clips=[_clip(entity_ops=[on])]), w, 3)
    hero = w.entities["lego_man"]
    assert hero.look == hero.look_short == helmeted and hero.look_since == 3, r.applied
    assert any(a.startswith("update lego_man: look=") for a in r.applied)
    p = _emit(_beat(clips=[_clip(action="He nods.")]), w, 4)
    assert f"On screen: Lego Man ({helmeted})." in p and "bare yellow head" not in p, "the whole new look, uncut"
    assert f"· looks: {helmeted}" in format_world_now(w)
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="shield", description="a red LEGO shield")])]), w, 5)
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="shield", description="a blue LEGO shield")])]), w, 6)
    assert w.entities["shield"].look == "a red LEGO shield"
    paint = EntityOp(op="update", entity="shield", description="a red LEGO shield with a white star painted on it")
    _apply(_beat(clips=[_clip(entity_ops=[paint])]), w, 7)
    assert w.entities["shield"].look_short == "a red LEGO shield with a white star painted on it"
    assert "shield (prop): shield · looks: a red LEGO shield with a white star" in format_world_now(w)
    plain = "the plain LEGO minifigure: bare yellow head, plain red torso, plain blue legs"
    off = EntityOp(op="update", entity="lego_man", kind="character", description=plain)
    _apply(_beat(clips=[_clip(entity_ops=[off])]), w, 8)
    assert hero.look_short == plain and hero.look_since == 8


def test_a_newcomers_short_look_ends_on_a_whole_phrase():
    w = _world()
    look = "A small LEGO forest traveler with a dark green hood, brown torso and a handcart wheel beside them."
    traveler = EntityOp(op="introduce", entity="forest traveler", kind="character", description=look, voice="dry")
    _apply(_beat(clips=[_clip(on_screen=["lego_man", "forest traveler"], entity_ops=[traveler])]), w, 2)
    assert w.entities["forest_traveler"].look_short == "A small LEGO forest traveler with a dark green hood"
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


def test_compiler_opening_clip_opens_on_the_first_frame():
    fz = FrameZero.from_json("tests/fixtures/lego-void")
    w = WorldModel.from_frame_zero(fz)
    beat = _beat(clips=[_clip(action="He waves.", ambient_sound="a faint plastic rattle", end_state="Lego Man mid-wave",
                              dialogue=[DialogueLine(speaker="lego_man", line="Hello?")])])
    prompt = compile_clip(beat, 0, w, scene=fz.scene, opening=True, segment_no=1)
    assert prompt.startswith("Setting: The White Void (an infinite, featureless"), "no style paragraph: the place's look"
    assert "Lego Man (the most default LEGO minifigure" in prompt and "claw hands" in prompt, "the full look"
    assert "The shot opens on this picture: Lego Man standing upright in a wide product shot" in prompt
    assert "A faint plastic rattle." in prompt and "Medium shot; the camera remains static." in prompt
    assert "Entering the picture" not in prompt, "the first frame already shows the character"
    assert 'Lego Man says, "Hello?"' in prompt, "no voice yet: the line as written"
    assert "Final frame: Lego Man mid-wave." in prompt


def test_compiler_continuing_clip_is_compact():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball")])]), w, 1)
    beat = _beat(clips=[
        _clip(action="He kicks.", end_state="mid-kick", on_screen=["lego_man", "soccer_ball"]),
        _clip(action="He watches the ball roll.", end_state="watching", on_screen=["lego_man", "soccer_ball"], camera_motion="slow push in"),
    ])
    p1 = compile_clip(beat, 0, w, segment_no=2)
    assert p1.startswith("Setting: The White Void ("), "no style or anchor: the place's look opens the prompt"
    assert f"Lego Man ({LEGO_SHORT})" in p1 and "claw hands" not in p1, "short look on continues"
    assert "soccer ball (a studded LEGO ball)" in p1, "a prop already introduced carries its short look"
    assert "The shot opens" not in p1 and "Cut to" not in p1
    p2 = compile_clip(beat, 1, w, segment_no=3)
    assert "Medium shot; the camera makes a slow push in." in p2
    beat3 = _beat(clips=[_clip(action="A cone appears.", end_state="cone", on_screen=["lego_man", "cone"],
                               entity_ops=[EntityOp(op="introduce", entity="cone", description="a yellow LEGO cone")])])
    _apply(beat3, w, 4)
    p3 = compile_clip(beat3, 0, w, segment_no=4)
    assert "Entering the picture during this clip: cone (a yellow LEGO cone). A cone appears." in p3
    assert f"On screen: Lego Man ({LEGO_SHORT})" in p3 and "; cone" not in p3, "an entering entity is not listed as present"


def test_compiler_describes_entering_and_returning_entities():
    w = _world()
    p1 = _emit(_beat(clips=[_clip(action="He looks around.", on_screen=["lego_man"])]), w, 1, opening=True)
    assert "On screen: Lego Man (the most default" in p1 and "claw hands" in p1 and "Entering" not in p1
    ball = _beat(clips=[_clip(action="A soccer ball rolls in and stops at his feet.", on_screen=["lego_man", "soccer_ball"],
                              entity_ops=[EntityOp(op="introduce", entity="soccer ball", description="a studded LEGO ball")])])
    p2 = _emit(ball, w, 2)
    assert f"On screen: Lego Man ({LEGO_SHORT})." in p2 and "soccer ball" not in p2.split("Entering")[0]
    assert "Entering the picture during this clip: soccer ball (a studded LEGO ball). A soccer ball rolls in" in p2
    p3 = _emit(_beat(clips=[_clip(action="He nudges the ball.", on_screen=["lego_man", "soccer_ball"])]), w, 3)
    assert f"On screen: Lego Man ({LEGO_SHORT}); soccer ball (a studded LEGO ball)." in p3
    assert "Entering" not in p3
    crown = _beat(clips=[_clip(action="The crown pops off with a click. He holds it up.", on_screen=["lego_man"],
                               entity_ops=[EntityOp(op="introduce", entity="princess crown", description="a bright, blocky LEGO crown", state="in Lego Man's hand")])])
    p4 = _emit(crown, w, 4)
    assert "Entering the picture during this clip: princess crown (a bright, blocky LEGO crown). The crown pops off" in p4
    assert f"On screen: Lego Man ({LEGO_SHORT})." in p4 and "soccer ball" not in p4
    p5 = _emit(_beat(clips=[_clip(action="He turns the crown over.", on_screen=["lego_man", "princess_crown"])]), w, 5)
    assert "; princess crown (a bright, blocky LEGO crown)." in p5 and "Entering" not in p5
    p6 = _emit(_beat(clips=[_clip(action="He kicks the ball, crown still in hand.", on_screen=["lego_man", "princess_crown", "soccer_ball"])]), w, 6)
    assert "Entering the picture during this clip: soccer ball (a studded LEGO ball). He kicks the ball" in p6
    assert f"On screen: Lego Man ({LEGO_SHORT}); princess crown (a bright, blocky LEGO crown)." in p6
    p7 = _emit(_beat(clips=[_clip(action="The ball sits alone; he is out of frame.", end_state="the ball alone", on_screen=["soccer_ball"])]), w, 7)
    assert "On screen: soccer ball (a studded LEGO ball)." in p7 and "Lego Man" not in p7
    p8 = _emit(_beat(clips=[_clip(action="He walks back in carrying a hat.", on_screen=["lego_man", "soccer_ball", "hat"],
                               entity_ops=[EntityOp(op="introduce", entity="hat", description="a red LEGO cap")])]), w, 8)
    assert "Entering the picture during this clip: Lego Man (the most default LEGO minifigure" in p8 and "claw hands" in p8, \
        "characters first, full look"
    assert "; hat (a red LEGO cap). He walks back in" in p8 and "On screen: soccer ball (a studded LEGO ball)." in p8
    dup = _beat(clips=[_clip(action="A brick-built dog, a squat grey LEGO dog with a stud nose, trots in.", on_screen=["lego_man", "soccer_ball", "hat", "dog"],
                             entity_ops=[EntityOp(op="introduce", entity="dog", description="a squat grey LEGO dog with a stud nose")])])
    p9 = _emit(dup, w, 9)
    assert "Entering the picture during this clip: dog. A brick-built dog" in p9 and p9.count("stud nose") == 1
    off = _beat(clips=[_clip(action="A bark from somewhere off screen.", on_screen=["lego_man", "dog"],
                             entity_ops=[EntityOp(op="introduce", entity="unseen cat", description="a LEGO cat", present=False)])])
    p10 = _emit(off, w, 10)
    assert "cat" not in p10 and "Entering" not in p10
    w2 = _world()
    p = compile_clip(_beat(clips=[_clip(on_screen=["lego_man"])]), 0, w2, segment_no=5)
    assert "On screen: Lego Man (" in p and "Entering" not in p


def test_persistence_round_trip():
    w = _world()
    _apply(_beat(clips=[_clip(entity_ops=[EntityOp(op="introduce", entity="hat", description="a red cap")])]), w, 1)
    w.events.append(EventRecord(segment=1, narration="n", beat=1, beat_intent="a hat"))
    with tempfile.TemporaryDirectory() as d:
        w.save(d)
        w.save(d)
        loaded = WorldModel.load(d)
        assert "hat" in loaded.entities and len(loaded.events) == 1 and loaded.events[0].beat_intent == "a hat"
        assert (Path(d) / "events.jsonl").read_text().count("\n") == 1
