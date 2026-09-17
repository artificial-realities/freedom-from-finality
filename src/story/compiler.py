"""Prompt compiler: turns one clip of a beat into the video model's dialect.

This is the only place that knows how LTX-2.5 likes to be prompted: one
flowing paragraph with the sound, the camera, who is on screen, the
action, dialogue in quotes and the image the clip ends on. Every clip
continues the previous one, so the prompt describes the next few seconds
of motion, not the picture the conditioning frames already carry: the
full visual style and the full look of a character go only on the
opening clip of the run; a prop's description goes only on the clip
that introduces it. Later clips carry the blueprint's short visual
anchor and each character's short look, which hold framing and identity
over long chains. When the model changes, this file changes; the schemas
do not.
"""

from __future__ import annotations

from src.story.schemas import BeatPlan
from src.story.world import Entity, WorldModel


def _sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if text[-1] not in ".!?\"'":
        text += "."
    return text[0].upper() + text[1:]


def _tail(text: str) -> str:
    """Like `_sentence` but keeps the original capitalisation (proper names)."""
    text = " ".join(text.split()).strip()
    if text and text[-1] not in ".!?\"'":
        text += "."
    return text


def _first_sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    for stop in (". ", "; "):
        cut = text.find(stop)
        if cut > 0:
            return text[: cut + 1].strip()
    return text


def describe(entity: Entity, *, full: bool) -> str:
    """The entity as the prompt names it: with its full look when `full`
    (the opening clip, or the clip that introduces it), otherwise a
    character's short look or just the name."""
    if full:
        look = " ".join(entity.render_look().split()).rstrip(".")
        if look and look.lower() != entity.name.lower():
            return f"{entity.name} ({look})"
        return entity.name
    short = " ".join(entity.look_short.split()).rstrip(".")
    if short:
        return f"{entity.name} ({short})"
    return entity.name


DESCRIBED_IN_FULL = 6   # on-screen entities described; the rest by name only


def compile_clip(
    beat: BeatPlan,
    index: int,
    world: WorldModel,
    *,
    visual_style: str,
    visual_anchor: str = "",
    narration_mode: str,
    voiceover_style: str = "",
    opening: bool = False,
    segment_no: int = 0,
) -> str:
    """The prompt for clip `index` of `beat`. `opening` marks the first
    clip of a run: the renderer seeds it from the reference image, so it
    carries the full style, the full looks and the reference pose.
    `segment_no` lets a prop's description ride on the clip that
    introduces it and nowhere else."""
    clip = beat.clips[index]
    parts: list[str] = []
    if clip.ambient_sound.strip():
        parts.append(_sentence(clip.ambient_sound))
    framing = " ".join(beat.framing.split()).rstrip(".") or "medium shot"
    motion = " ".join(clip.camera_motion.split()).rstrip(".") or "static"
    parts.append(_sentence(f"{framing}, {motion} camera"))

    ents = [e for e in (world.resolve(eid) for eid in clip.on_screen)
            if e is not None and e.kind != "location"]
    ents.sort(key=lambda e: e.kind != "character")  # characters first
    named = [describe(e, full=opening or e.first_seen == segment_no) for e in ents[:DESCRIBED_IN_FULL]]
    named += [e.name for e in ents[DESCRIBED_IN_FULL:]]
    if named:
        parts.append("On screen: " + "; ".join(named) + ".")
    # The first clip starts from the reference image, so it must open from
    # what that image shows; a pose mismatch in the first frames is what
    # drags the picture off the sheet (2026-09-08 sweep).
    if opening:
        for ent in ents:
            if ent.reference_pose:
                parts.append(f"The shot opens with {ent.name} {ent.reference_pose.strip().rstrip('.')}, then the action begins.")
                break
    if clip.action.strip():
        parts.append(_sentence(clip.action))
    for line in clip.dialogue:
        speaker = world.resolve(line.speaker)
        name = speaker.name if speaker is not None else line.speaker
        delivery = f" ({line.delivery.strip()})" if line.delivery.strip() else ""
        text = line.line.strip().replace('"', "'")
        parts.append(f'{name} says{delivery}: "{text}"')
    if clip.end_state.strip():
        parts.append("Final frame: " + _tail(clip.end_state))
    paragraphs = [" ".join(p for p in parts if p)]

    if index == 0 and beat.voiceover.strip() and narration_mode == "ltx":
        line = " ".join(beat.voiceover.split()).replace('"', "'")
        hint = f", {voiceover_style.strip()}" if voiceover_style.strip() else ""
        paragraphs.append(f'A narrator\'s voice-over (English{hint}) says: "{line}"')

    if opening:
        style = " ".join(visual_style.split())
    else:
        style = " ".join(visual_anchor.split()) or _first_sentence(visual_style)
    return ((style + " " if style else "") + " ".join(paragraphs)).strip()
