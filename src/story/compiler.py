"""Prompt compiler: turns a Segment into the video model's dialect.

This is the only place that knows how LTX-2.5 likes to be prompted
(one flowing paragraph: sound, camera, who is on screen described the
same way every time, the action in order, dialogue in quotes, and the
image the shot ends on; cuts re-establish the shot and re-identify the
characters). When the model changes, this file changes; the Segment
schema does not.
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
    look = " ".join(entity.render_look().split())
    if look and look.lower() != entity.name.lower():
        return f"{entity.name} ({look})"
    return entity.name


def compile_prompt(
    segment: Segment,
    world: WorldModel,
    *,
    visual_style: str,
    narration_mode: str,
    voiceover_style: str = "",
) -> tuple[str, int]:
    """Return (prompt, total_duration_seconds)."""
    paragraphs: list[str] = []
    total = 0
    last = len(segment.shots) - 1

    for i, shot in enumerate(segment.shots):
        parts: list[str] = []
        if shot.ambient_sound.strip():
            parts.append(_sentence(shot.ambient_sound))
        framing = " ".join(shot.framing.split()).rstrip(".") or "medium shot"
        motion = " ".join(shot.camera_motion.split()).rstrip(".") or "static"
        if i == 0:
            parts.append(_sentence(f"{framing}, {motion} camera"))
        else:
            parts.append(_sentence(f"Cut to a {framing}, {motion} camera"))
        named = []
        for eid in shot.on_screen:
            ent = world.resolve(eid)
            if ent is not None and ent.kind != "location":
                named.append(describe(ent))
        if named:
            parts.append("On screen: " + "; ".join(named) + ".")
        for beat in shot.action_beats:
            if beat.strip():
                parts.append(_sentence(beat))
        if i == last:
            for line in segment.dialogue:
                speaker = world.resolve(line.speaker)
                name = speaker.name if speaker is not None else line.speaker
                delivery = f" ({line.delivery.strip()})" if line.delivery.strip() else ""
                text = line.line.strip().replace('"', "'")
                parts.append(f'{name} says{delivery}: "{text}"')
        if shot.end_state.strip():
            parts.append("The shot ends on " + _tail(shot.end_state))
        paragraphs.append(" ".join(p for p in parts if p))
        total += shot.duration_seconds

    if segment.voiceover.strip() and narration_mode == "ltx":
        line = " ".join(segment.voiceover.split()).replace('"', "'")
        hint = f", {voiceover_style.strip()}" if voiceover_style.strip() else ""
        paragraphs.append(f'A narrator\'s voice-over (English{hint}) says: "{line}"')

    style = " ".join(visual_style.split())
    prompt = (style + " " if style else "") + " ".join(paragraphs)
    return prompt.strip(), max(3, min(total, 15))
