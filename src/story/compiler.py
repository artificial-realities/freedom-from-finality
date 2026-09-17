"""Prompt compiler: turns a Segment into the video model's dialect.

This is the only place that knows how LTX-2.5 likes to be prompted
(one flowing paragraph: sound, camera, who is on screen described the
same way every time, the action in order, dialogue in quotes, and the
image the shot ends on). Every clip continues the previous one; the
first clip of a run is seeded from the reference image, so that prompt
opens from the pose the cast sheet says the image shows. When the model
changes, this file changes; the Segment schema does not.
"""

from __future__ import annotations

from src.story.schemas import Segment
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


def describe(entity: Entity) -> str:
    look = " ".join(entity.render_look().split()).rstrip(".")
    if look and look.lower() != entity.name.lower():
        return f"{entity.name} ({look})"
    return entity.name


DESCRIBED_IN_FULL = 6   # on-screen entities re-identified with their look; the rest by name


def compile_prompt(
    segment: Segment,
    world: WorldModel,
    *,
    visual_style: str,
    narration_mode: str,
    voiceover_style: str = "",
    clip_seconds: int = 0,
    opening: bool = False,
) -> tuple[str, int]:
    """Return (prompt, duration_seconds). With `clip_seconds` set the
    duration is that, whatever the shot asks for. `opening` marks the
    first clip of a run: the renderer seeds it from the reference image,
    so the prompt opens from the reference pose before the action begins."""
    shot = segment.shot
    parts: list[str] = []
    if shot.ambient_sound.strip():
        parts.append(_sentence(shot.ambient_sound))
    framing = " ".join(shot.framing.split()).rstrip(".") or "medium shot"
    motion = " ".join(shot.camera_motion.split()).rstrip(".") or "static"
    parts.append(_sentence(f"{framing}, {motion} camera"))
    ents = [e for e in (world.resolve(eid) for eid in shot.on_screen)
            if e is not None and e.kind != "location"]
    ents.sort(key=lambda e: e.kind != "character")  # characters always described
    named = [describe(e) for e in ents[:DESCRIBED_IN_FULL]] + [e.name for e in ents[DESCRIBED_IN_FULL:]]
    if named:
        parts.append("On screen: " + "; ".join(named) + ".")
    # The first clip starts from the reference image, so the shot must open
    # from what that image shows; a pose mismatch in the first frames is
    # what drags the picture off the sheet (2026-09-08 sweep).
    if opening:
        for ent in ents:
            if ent.reference_pose:
                parts.append(f"The shot opens with {ent.name} {ent.reference_pose.strip().rstrip('.')}, then the action begins.")
                break
    for beat in shot.action_beats:
        if beat.strip():
            parts.append(_sentence(beat))
    for line in segment.dialogue:
        speaker = world.resolve(line.speaker)
        name = speaker.name if speaker is not None else line.speaker
        delivery = f" ({line.delivery.strip()})" if line.delivery.strip() else ""
        text = line.line.strip().replace('"', "'")
        parts.append(f'{name} says{delivery}: "{text}"')
    if shot.end_state.strip():
        parts.append("Final frame: " + _tail(shot.end_state))
    paragraphs = [" ".join(p for p in parts if p)]

    if segment.voiceover.strip() and narration_mode == "ltx":
        line = " ".join(segment.voiceover.split()).replace('"', "'")
        hint = f", {voiceover_style.strip()}" if voiceover_style.strip() else ""
        paragraphs.append(f'A narrator\'s voice-over (English{hint}) says: "{line}"')

    style = " ".join(visual_style.split())
    prompt = (style + " " if style else "") + " ".join(paragraphs)
    if clip_seconds > 0:
        return prompt.strip(), clip_seconds
    return prompt.strip(), max(3, min(shot.duration_seconds, 15))
