"""The story's cast and perspective policy (ARCH-19): a protagonist who
never drifts, supporting characters with entrance conditions, dynamic
characters registered once with everything that keeps them the same,
and a perspective the validator holds the Director to. The synthetic
story in `tests/fixtures/lighthouse` exercises every branch; the Lego
story is the protagonist-only case."""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.director import SYSTEM_PATH
from src.models.story import Story
from src.story.compiler import compile_clip
from src.story.context import format_cast, format_cast_rules, format_world_now
from src.story.schemas import BeatPlan, ClipSpec, EntityOp
from src.story.validator import apply_clip, check
from src.story.world import WorldModel

LIGHTHOUSE = "tests/fixtures/lighthouse"


def _world(path: str = LIGHTHOUSE) -> WorldModel:
    return WorldModel.from_story(Story.from_json(path))


def _beat(*ops: EntityOp, on_screen=("mara",), **over) -> BeatPlan:
    base = dict(intent="t", framing="medium shot",
                clips=[ClipSpec(action="Something happens.", end_state="after", on_screen=list(on_screen),
                                entity_ops=list(ops))])
    base.update(over)
    return BeatPlan(**base)


def test_the_blueprint_names_its_protagonist_and_supporting_cast():
    """The protagonist is named (not merely first), starts in the scene in
    its written state; a supporting character waits with its condition;
    the cast sheet carries role, personality, what never changes and the
    entrance condition."""
    w = _world()
    assert w.protagonist == "mara"
    mara, tomas = w.entities["mara"], w.entities["tomas"]
    assert mara.present and mara.location == "lamp_room" and mara.now() == "standing; watching the sea; at the lamp room window"
    assert mara.invariants == ["grey hair", "the navy fisherman's sweater", "the brass key on a cord at her neck"]
    assert not tomas.present and tomas.appears == "must" and tomas.appears_when == "the lamp fails for the first time"
    assert tomas.role == "the nephew who has come to close the station" and mara.appears == ""
    cast = format_cast(w)
    assert "Never changes: grey hair; the navy fisherman's sweater; the brass key on a cord at her neck." in cast
    assert "Not in the film at the start; must appear, when the lamp fails for the first time." in cast
    assert "Personality: Impatient, kind underneath" in cast
    assert "`tomas` (not yet in the film)" in format_world_now(w)
    rules = format_cast_rules(w)
    assert "Protagonist: `mara` (Mara)" in rules and "New characters beyond the cast are allowed. Only adults" in rules
    assert "A beat may switch to first-person" in rules and "telescope" in rules


def test_lego_is_protagonist_only_and_old_blueprints_keep_their_lock():
    """The rollback form: a story without new characters. A blueprint from
    before ARCH-19 reads `cast_locked` into the policy."""
    lego = Story.from_json("stories/lego-void")
    assert lego.cast_policy.dynamic_characters is False and lego.protagonist == "Lego Man"
    w = WorldModel.from_story(lego)
    assert w.cast_locked and "No characters beyond the cast" in format_cast_rules(w)
    assert "the film is shot third-person, always" in format_cast_rules(w)
    raw = json.loads(Path("stories/lego-void/story.json").read_text())
    for key in ("cast_policy", "protagonist"):
        raw.pop(key)
    assert Story(**{**raw, "cast_locked": True}).cast_policy.dynamic_characters is False
    assert Story(**{**raw, "cast_locked": False}).cast_policy.dynamic_characters is True
    r = check(_beat(EntityOp(op="introduce", entity="Lego Woman", kind="character", description="a second figure"),
                    on_screen=("lego_man",)), w)
    assert any("allows no characters beyond its cast (`lego_man`)" in v for v in r.violations), r.violations


def test_a_dynamic_character_is_registered_once_and_stays_the_same():
    """Introduced where the story allows it: registered with its look, a
    short look for later clips, role, whether it speaks, its voice and its
    state; described on entering, carried by its short look after; listed
    in World now (not on the cached cast sheet)."""
    w = _world()
    system_before = format_cast(w) + format_cast_rules(w)
    sailor = EntityOp(op="introduce", entity="old sailor", kind="character",
                      description="a weathered old sailor in a black sou'wester and a torn orange lifejacket; one eye clouded",
                      role="a fisherman whose boat went down off the Skerry", speaks=True, voice="rough, cracked baritone",
                      posture="leaning in the doorway", activity="dripping, catching his breath", position="frame right")
    beat = _beat(sailor, on_screen=("mara", "old sailor"))
    assert not check(beat, w).violations
    apply_clip(beat, 0, w, segment_no=12)
    ent = w.entities["old_sailor"]
    assert ent.dynamic and ent.first_seen == 12 and ent.present and ent.speaks
    assert ent.role == "a fisherman whose boat went down off the Skerry" and ent.voice.description == "rough, cracked baritone"
    assert ent.look_short == "a weathered old sailor in a black sou'wester and a torn orange lifejacket"
    assert ent.now() == "leaning in the doorway; dripping, catching his breath; frame right"
    prompt = compile_clip(beat, 0, w, visual_style="", narration_mode="ltx", segment_no=12)
    assert "Entering the picture during this clip: old sailor (a weathered old sailor in a black sou'wester" in prompt
    later = _beat(on_screen=("mara", "old_sailor"))
    apply_clip(later, 0, w, segment_no=13)
    prompt = compile_clip(later, 0, w, visual_style="", narration_mode="ltx", segment_no=13)
    assert "old sailor (a weathered old sailor in a black sou'wester and a torn orange lifejacket)" in prompt, \
        "identity restated on every later clip"
    assert format_cast(w) + format_cast_rules(w) == system_before, "the system prompt stays byte-identical (cache)"
    now = format_world_now(w)
    assert "old_sailor (character): old sailor" in now and "introduced at segment 12" in now
    assert "role: a fisherman whose boat went down off the Skerry" in now and "speaks, voice: rough, cracked baritone" in now


def test_the_validator_protects_the_protagonist_and_the_policy():
    """A new character without a look, a second copy of the protagonist and
    the protagonist's removal are violations and are not applied; a
    supporting character introduced by name is brought in, not duplicated."""
    w = _world()
    faceless = EntityOp(op="introduce", entity="harbourmaster", kind="character")
    double = EntityOp(op="introduce", entity="Mara 2", kind="character", description="another keeper")
    gone = EntityOp(op="remove", entity="mara")
    r = check(_beat(faceless, double, gone), w)
    words = " ".join(r.violations)
    assert "needs a `description`" in words and "never appears twice" in words and "cannot be removed" in words
    apply_clip(_beat(faceless, double, gone), 0, w, segment_no=3)
    assert "harbourmaster" not in w.entities and "mara_2" not in w.entities and not w.entities["mara"].gone
    tomas = EntityOp(op="introduce", entity="Tomas", kind="character", description="her nephew")
    r = check(_beat(tomas, on_screen=("mara", "tomas")), w)
    assert not r.violations and any("already exists as tomas; treated as update" in x for x in r.warnings)
    apply_clip(_beat(tomas, on_screen=("mara", "tomas")), 0, w, segment_no=4)
    assert w.entities["tomas"].present and "tomas_2" not in w.entities and not w.entities["tomas"].dynamic


def test_the_perspective_policy():
    """A permitted switch passes and the compiler shoots it first-person; a
    perspective the story does not allow is a violation; empty is the
    default. The Director's system prompt carries the rules."""
    w = _world()
    assert not check(_beat(perspective="First person"), w).violations
    prompt = compile_clip(_beat(perspective="first-person"), 0, w, visual_style="", narration_mode="ltx", segment_no=5)
    assert "First-person point of view, medium shot; the camera remains static." in prompt
    r = check(_beat(perspective="bird's-eye"), w)
    assert any("perspective 'bird's-eye' is not one this story allows (third-person, first-person)" in v
               for v in r.violations), r.violations
    third = compile_clip(_beat(), 0, w, visual_style="", narration_mode="ltx", segment_no=5)
    assert "point of view" not in third
    lego = WorldModel.from_story(Story.from_json("stories/lego-void"))
    assert check(_beat(perspective="first-person", on_screen=("lego_man",)), lego).violations
    text = SYSTEM_PATH.read_text()
    assert "{cast_rules}" in text and "`perspective`" in text and "`role`, `speaks` and `voice`" in text
