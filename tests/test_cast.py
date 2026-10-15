"""The story's cast (ARCH-19), on the Lego story: a protagonist who never
drifts, a supporting character with an entrance condition, newcomers
registered once with everything that keeps them the same, voices that
belong to the story, a perspective the validator holds the Director to,
and a reference image that says who is in the opening picture."""

from __future__ import annotations

from src.agents.director import SYSTEM_PATH
from src.models.story import Picture, Reference, Story, Voice
from src.story.compiler import compile_clip, speech
from src.story.context import format_cast, format_cast_rules, format_film, format_picture, format_world_now
from src.story.schemas import BeatPlan, ClipSpec, DialogueLine, EntityOp
from src.story.validator import apply_clip, check
from src.story.world import WorldModel

LEGO = "stories/lego-void"
PLAIN = Picture()


def _story() -> Story:
    return Story.from_json(LEGO)


def _world(story: Story | None = None) -> WorldModel:
    return WorldModel.from_story(story or _story())


def _beat(*ops: EntityOp, on_screen=("lego_man",), dialogue=(), **over) -> BeatPlan:
    base = dict(intent="t", framing="medium shot",
                clips=[ClipSpec(action="Something happens.", end_state="after", on_screen=list(on_screen),
                                entity_ops=list(ops), dialogue=list(dialogue))])
    base.update(over)
    return BeatPlan(**base)


def test_the_blueprint_names_its_protagonist_and_supporting_cast():
    """The protagonist is named, starts in the scene in its written state;
    the Spaceman waits with his condition; the cast sheet carries who each
    is, what they want, their nature, the entrance, the look and the voice."""
    w = _world()
    lego, spaceman = w.entities["lego_man"], w.entities["spaceman"]
    assert w.protagonist == "lego_man" and lego.present and lego.location == "the_white_void"
    assert not spaceman.present and spaceman.appears == "must" and lego.appears == ""
    cast = format_cast(w)
    assert "- `spaceman`: Spaceman. A classic LEGO Space astronaut" in cast
    assert "  Wants: To plant his flag and claim the void for science." in cast
    assert "  Nature: Brisk and by the book" in cast
    assert "Not in the film at the start; must appear, when the first time one of Lego Man's bits fails badly." in cast
    assert "  Voice: crisp, clipped, with a faint radio crackle; speaks English." in cast
    assert "`spaceman` (not yet in the film)" in format_world_now(w)
    rules = format_cast_rules(w)
    assert "Protagonist: `lego_man` (Lego Man)" in rules and "its look held in every clip" in rules
    assert "New characters beyond the cast are allowed: Other minifigures from any LEGO theme" in rules
    assert "A beat may switch to first-person" in rules and "binoculars" in rules


def test_the_film_block_says_only_what_the_story_gives():
    """Both roles read the story and the storyteller; empty fields add no
    text, and only the Showrunner hears how the film opens."""
    story = _story()
    film = format_film(story)
    assert film.startswith("Title: Lego Man in the White Void\nPremise: A LEGO minifigure alone")
    assert "The question the film keeps open: Does he ever land the perfect bit?" in film
    assert "What it is about: Play for its own sake" in film and "How the film opens" not in film
    assert "This storyteller would never:\n- Turn cynical, or wink at the audience." in film
    assert "How the film opens: He wakes in the empty white space" in format_film(story, opening=True)
    bare = Story(title="T", premise="P.")
    assert format_film(bare, opening=True) == "Title: T\nPremise: P."


def test_a_story_without_newcomers_keeps_its_cast():
    story = _story()
    w = _world(story.model_copy(update={"cast": story.cast.model_copy(update={"newcomers": ""})}))
    assert w.cast_locked and "No characters beyond the cast" in format_cast_rules(w)
    r = check(_beat(EntityOp(op="introduce", entity="Lego Woman", kind="character", description="a second figure")), w)
    assert any("allows no characters beyond its cast (`lego_man`, `spaceman`)" in v for v in r.violations), r.violations


def test_a_newcomer_is_registered_once_and_stays_the_same():
    """Introduced where the story allows it: registered with its look, a
    short look for later clips, who it is, its voice and its state; it may
    speak on the clip it enters; described on entering, carried by its
    short look after; listed in World now (not on the cached cast sheet)."""
    w = _world()
    system_before = format_cast(w) + format_cast_rules(w)
    pirate = EntityOp(op="introduce", entity="pirate captain", kind="character",
                      description="a LEGO pirate captain minifigure in a black tricorn hat and red coat; a peg leg",
                      who="a castaway who thinks the void is a becalmed sea", voice="gravelly, booming",
                      state="standing on his peg leg at frame right, squinting at the horizon")
    hello = DialogueLine(speaker="pirate captain", line="Ahoy!")
    beat = _beat(pirate, on_screen=("lego_man", "pirate captain"), dialogue=[hello])
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
    later = _beat(on_screen=("lego_man", "pirate_captain"))
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
    r = check(_beat(spaceman, on_screen=("lego_man", "spaceman")), w)
    assert not r.violations and any("already exists as spaceman; treated as update" in x for x in r.warnings)
    apply_clip(_beat(spaceman, on_screen=("lego_man", "spaceman")), 0, w, segment_no=4)
    assert w.entities["spaceman"].present and "spaceman_2" not in w.entities and not w.entities["spaceman"].dynamic


def test_voices_are_the_storys():
    """LTX-05: a character's voice (a string or language, accent, delivery
    and vocal characteristics) travels from the blueprint into the world;
    dialogue is compiled in LTX-2's single-speaker form with the line's
    delivery first, the character's voice after, then language and accent."""
    w = _world()
    w.entities["spaceman"].voice = Voice(description="a crisp voice", language="Norwegian",
                                         accent="a soft Bergen accent", delivery="clipped")
    beat = _beat(on_screen=("lego_man", "spaceman"), dialogue=[
        DialogueLine(speaker="spaceman", line='Contact at "two" o\'clock.', delivery="urgent"),
        DialogueLine(speaker="lego_man", line="Where?")])
    prompt = compile_clip(beat, 0, w, picture=PLAIN, segment_no=0)
    assert ("Spaceman says in an urgent, crisp voice, in Norwegian with a soft Bergen accent, "
            "\"Contact at 'two' o'clock.\"") in prompt, prompt
    assert 'Lego Man says in an earnest, slightly formal, a little breathless voice, in English, "Where?"' in prompt
    assert speech("Ola", "Hei.", voice=Voice(delivery="calm")) == 'Ola says in a calm voice, "Hei."'
    assert speech("Ola", "Hei.") == 'Ola says, "Hei."'
    assert Story(title="t", premise="p", cast={"characters": [{"name": "Ola", "voice": "bright"}]}) \
        .cast.characters[0].voice == Voice(description="bright"), "a plain string is the voice's description"


def test_the_perspective_policy():
    """A permitted switch passes and the compiler shoots it first-person; a
    perspective the story does not allow is a violation; empty is the
    default. The Director's system prompt carries the rules."""
    w = _world()
    assert not check(_beat(perspective="First person"), w).violations
    prompt = compile_clip(_beat(perspective="first-person"), 0, w, picture=PLAIN, segment_no=5)
    assert "First-person point of view, medium shot; the camera remains static." in prompt
    r = check(_beat(perspective="bird's-eye"), w)
    assert any("perspective 'bird's-eye' is not one this story allows (third-person, first-person)" in v
               for v in r.violations), r.violations
    assert "point of view" not in compile_clip(_beat(), 0, w, picture=PLAIN, segment_no=5)
    story = _story()
    fixed = story.model_copy(update={"picture": story.picture.model_copy(update={"perspective": PLAIN.perspective})})
    assert check(_beat(perspective="first-person"), _world(fixed)).violations
    text = SYSTEM_PATH.read_text()
    assert "{cast_rules}" in text and "`perspective`" in text and "`who` and `voice`" in text


def test_the_reference_says_who_is_in_the_opening_picture():
    """The opening clip opens on what the reference shows; a character it
    names is already in the picture, any other on screen enters during the
    clip; without a reference nothing is said about the frame before."""
    story = _story()
    both = _beat(on_screen=("lego_man", "spaceman"))
    prompt = compile_clip(both, 0, _world(), picture=story.picture, opening=True, segment_no=1)
    assert "On screen: Lego Man (the most default LEGO minifigure" in prompt
    assert "The shot opens with Lego Man standing upright in a wide product shot" in prompt
    assert "Entering the picture during this clip: Spaceman (a classic LEGO Space astronaut minifigure" in prompt
    assert "a reference image that shows Lego Man standing upright" in format_picture(story.picture)

    beach = story.picture.model_copy(update={"reference": Reference(image="beach.png", shows="an empty beach at dawn")})
    w = _world(story.model_copy(update={"picture": beach}))
    assert w.opening_picture == []
    prompt = compile_clip(_beat(), 0, w, picture=beach, opening=True, segment_no=1)
    assert "The shot opens with an empty beach at dawn, then the action begins." in prompt
    assert "Entering the picture during this clip: Lego Man (the most default" in prompt and "On screen" not in prompt

    none = story.picture.model_copy(update={"reference": None})
    w = _world(story.model_copy(update={"picture": none}))
    prompt = compile_clip(_beat(), 0, w, picture=none, opening=True, segment_no=1)
    assert w.opening_picture is None and "The shot opens" not in prompt and "Entering" not in prompt
    assert "There is no reference image" in format_picture(none)
