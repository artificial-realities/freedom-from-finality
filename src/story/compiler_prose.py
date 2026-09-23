"""The caption-prose compiler: LTX-01's experiment arm (off by default).

`compile_clip` here takes the same inputs as `src.story.compiler` and
writes the clip the way LTX-2.5's training captions read (review 02 §2.2,
§9): one paragraph, in this order:

1. a style clause (the blueprint's full `visual_style` on the opening
   clip, its `visual_anchor` after);
2. the picture the clip opens on, from the world model: each character on
   screen with its short look, posture, position and activity and what it
   holds or wears, each prop with its state, "exactly as the shot opens"
   (on the opening clip the reference pose is described, not announced);
3. the framing as prose: "A medium shot frames him straight-on, eye
   level, and the camera remains static" / "… as the camera slowly pushes
   in";
4. entering entities introduced with their look where the action first
   names them, or in an entrance sentence when it never does (LTX-03);
5. the action as written, with the clip's sound woven into its first
   sentence (LTX-06);
6. dialogue in LTX-2's single-speaker form;
7. "The shot ends with …";
8. on a quiet clip, "Apart from that, the scene is silent." (LTX-06);
9. on the beat's first clip, the narrator as an unseen voice in the same
   paragraph.

A word-count guard (`word_note`) says when a prompt is outside the
targets (opening 150-200 words, continuing 80-120).

`compiler: "prose"` in a config selects it; X-05 decides whether it
becomes the default (LTX-01 is EXPERIMENT_REQUIRED). The schemas do not
change.
"""

from __future__ import annotations

import logging
import re

from src.models.story import Narrator, perspective_key
from src.story.compiler import (
    DESCRIBED_IN_FULL,
    _voice_words,
    camera_clause,
    previous_picture,
    speech,
)
from src.story.schemas import BeatPlan
from src.story.world import Entity, WorldModel

logger = logging.getLogger(__name__)

OPENING_WORDS = (150, 200)
CONTINUING_WORDS = (80, 120)
_WORN = ("wear", "worn", "on his head", "on her head", "on its head", "on their head", "around his neck",
         "around her neck", "on his back", "on her back")


def _clean(text: str) -> str:
    return " ".join(text.split()).strip()


def _sentence(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""
    if text[-1] not in ".!?\"'":
        text += "."
    return text[0].upper() + text[1:]


_COMMON_OPENERS = {"the", "a", "an", "he", "she", "they", "it", "his", "her", "their", "its", "both",
                   "soft", "faint", "quiet", "loud", "gentle", "tiny", "small", "light", "plastic"}


def _lower_first(text: str) -> str:
    """Lower the first letter only when the first word is a common word, so
    a proper name ("Lego Man") keeps its capital."""
    text = _clean(text).rstrip(".")
    first = text.split(" ", 1)[0]
    if first.lower() in _COMMON_OPENERS:
        return first.lower() + text[len(first):]
    return text


def _look(entity: Entity, *, full: bool) -> str:
    look = entity.render_look() if full else (entity.look_short or "")
    look = _clean(look).rstrip(".")
    return "" if not look or look.lower() == entity.name.lower() else look


def _carried(world: WorldModel, who: Entity) -> list[str]:
    out = []
    for prop in world.held_props(who.id):
        status = prop.status.lower()
        verb = "wearing" if any(w in status for w in _WORN) else "holding"
        out.append(f"{verb} the {prop.name}")
    return out


def _opening_state(world: WorldModel, characters: list[Entity], props: list[Entity], *, opening: bool) -> str:
    """The picture the clip opens on, from the world model."""
    parts: list[str] = []
    held = {p.id for c in characters for p in world.held_props(c.id)}
    for c in characters:
        bits = [c.name]
        look = _look(c, full=opening)
        if look:
            bits.append(look)
        if opening and c.reference_pose:
            state = _clean(c.reference_pose).rstrip(".")
            bits.append(state)
        else:
            state = ", ".join(x for x in (c.posture, c.position, c.activity) if x)
            if state:
                bits.append(f"is {state}" if not state.startswith(("is ", "was ")) else state)
        bits.extend(_carried(world, c))
        parts.append(", ".join(bits))
    for p in props:
        if p.id in held:
            continue
        look = _look(p, full=False)
        name = f"the {p.name}" + (f", {look}," if look else "")
        parts.append(f"{name} {p.status}" if p.status else f"{name} in the picture")
    if not parts:
        return ""
    return _sentence("; ".join(parts) + ", exactly as the shot opens")


def _framing(beat: BeatPlan, world: WorldModel, clip, subject: str) -> str:
    parts = [x.strip() for x in _clean(beat.framing).rstrip(".").split(",") if x.strip()] or ["medium shot"]
    shot, view = parts[0], ", ".join(parts[1:])
    if (perspective_key(beat.perspective) or perspective_key(world.perspective.default)) == "first-person":
        shot = f"first-person point-of-view {shot}"
    camera = camera_clause(clip.camera_motion)
    tail = ", and the camera remains static" if camera.endswith("remains static") else f" as {camera}"
    article = "An" if shot[:1].lower() in "aeiou" else "A"
    return _sentence(f"{article} {_lower_first(shot)} frames {subject}" + (f" {view}" if view else "") + tail)


def _with_sound(action: str, sound: str) -> str:
    """The clip's sound as a clause of the action's first sentence."""
    action = _clean(action)
    sound = _lower_first(sound)
    if not sound:
        return _sentence(action)
    m = re.search(r"[.!?](\s|$)", action)
    if m is None:
        return _sentence(f"{action.rstrip('.')}, with {sound}")
    return _sentence(f"{action[:m.start()]}, with {sound}{action[m.start():]}")


def _entering(action: str, entities: list[Entity], described: dict[str, bool]) -> tuple[str, list[str]]:
    """Introduce each entering entity with its look where the action first
    names it; those it never names get an entrance sentence."""
    unnamed = []
    for e in entities:
        look = _look(e, full=True) if described.get(e.id, True) else ""
        if not look or look.lower() in action.lower():
            if not re.search(re.escape(e.name), action, re.I):
                unnamed.append(e.name)
            continue
        m = re.search(r"\b" + re.escape(e.name) + r"\b", action, re.I)
        if m:
            action = action[:m.end()] + f", {_lower_first(look)}," + action[m.end():]
        else:
            unnamed.append(look)        # the look itself is the subject of the entrance
    return action, unnamed


def word_note(prompt: str, *, opening: bool) -> str:
    """Where the prompt's length stands against the targets ("" = within)."""
    words = len(prompt.split())
    low, high = OPENING_WORDS if opening else CONTINUING_WORDS
    if words > 200:
        return f"{words} words: above the 200-word ceiling"
    if words < low:
        return f"{words} words: below the {low}-{high} target"
    if words > high:
        return f"{words} words: above the {low}-{high} target"
    return ""


def compile_clip(
    beat: BeatPlan,
    index: int,
    world: WorldModel,
    *,
    visual_style: str,
    visual_anchor: str = "",
    narration_mode: str,
    narrator: Narrator | None = None,
    opening: bool = False,
    segment_no: int = 0,
) -> str:
    """The prompt for clip `index` of `beat`, as one caption paragraph."""
    clip = beat.clips[index]
    ents: list[Entity] = []
    for ref in clip.on_screen:
        e = world.resolve(ref)
        if e is not None and e.kind != "location" and e not in ents:
            ents.append(e)
    previous = previous_picture(world, segment_no)
    entering = [e for e in ents if (segment_no > 0 and e.first_seen == segment_no) or
                (previous is not None and e.id not in previous)]
    if segment_no > 0:
        entering += [e for e in world.entities.values()
                     if e.first_seen == segment_no and e.present and not e.gone
                     and e.kind != "location" and e not in ents]
    staying = [e for e in ents if e not in entering]
    described = {e.id: i < DESCRIBED_IN_FULL for i, e in enumerate(
        sorted(entering + staying, key=lambda e: (e.kind != "character", e in staying)))}
    characters = [e for e in staying if e.kind == "character"]
    props = [e for e in staying if e.kind != "character"]
    subject = " and ".join(c.name for c in characters) if characters else "the scene"

    style = _clean(visual_style if opening else (visual_anchor or visual_style.split(". ")[0]))
    parts = [_sentence(style) if style else ""]
    parts.append(_opening_state(world, characters, props, opening=opening))
    parts.append(_framing(beat, world, clip, subject))
    action, unnamed = _entering(_clean(clip.action), entering, described)
    for name in unnamed:
        parts.append(_sentence(f"A moment later {_lower_first(name)} comes into the picture"))
    parts.append(_with_sound(action, clip.ambient_sound))
    for line in clip.dialogue:
        speaker = world.resolve(line.speaker)
        parts.append(speech(speaker.name if speaker else line.speaker, line.line, delivery=line.delivery.strip(),
                            voice=speaker.voice if speaker is not None else None))
    if clip.end_state.strip():
        parts.append(_sentence(f"By the end of the shot, {_lower_first(clip.end_state)}"))
    narrated = index == 0 and beat.voiceover.strip() and narration_mode == "ltx"
    if not clip.dialogue and not narrated:
        parts.append("Apart from that, the scene is silent." if clip.ambient_sound.strip() else "The scene is silent.")
    if narrated:
        voice = (narrator or Narrator()).voice
        described = re.sub(r"\s+narrator$", "", _voice_words(voice.description))
        how = ", ".join(x for x in (described, voice.accent.strip().rstrip(".")) if x)
        tongue = f", speaking {voice.language.strip()}," if voice.language.strip() else ""
        line = _clean(beat.voiceover).replace('"', "'")
        parts.append(f'An unseen narrator{" with a " + how + " voice" if how else ""}{tongue} says, "{line}"')
    prompt = " ".join(p for p in parts if p)
    note = word_note(prompt, opening=opening)
    if note:
        logger.info("prose compiler, segment %s: %s", segment_no, note)
    return prompt
