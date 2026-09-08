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
        ents = [e for e in (world.resolve(eid) for eid in shot.on_screen)
                if e is not None and e.kind != "location"]
        ents.sort(key=lambda e: e.kind != "character")  # characters always described
        named = [describe(e) for e in ents[:DESCRIBED_IN_FULL]] + [e.name for e in ents[DESCRIBED_IN_FULL:]]
        if named:
            parts.append("On screen: " + "; ".join(named) + ".")
        # A cut re-seeds the renderer from the reference image, so the shot
        # must open from what that image shows; a pose mismatch in the first
        # frames is what drags the picture off the sheet (2026-09-08 sweep).
        if shot.transition == "cut":
            for ent in ents:
                if ent.reference_pose:
                    parts.append(f"The shot opens with {ent.name} {ent.reference_pose.strip().rstrip('.')}, then the action begins.")
                    break
        # Visual memory: when the last observation saw a deviation, say what
        # must NOT be there, positively, so the prompt pulls back to the sheet.
        for ent in ents[:DESCRIBED_IN_FULL]:
            if ent.drift >= 2 and ent.observed_look and ent.observed_look != "matches the sheet":
                parts.append(f"{ent.name} is exactly as described, not {ent.observed_look}.")
        picture = world.picture
        if picture is not None and not picture.background_ok and picture.background_note:
            parts.append(f"The setting is exactly as described above, not {picture.background_note.strip()}.")
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
            parts.append("Final frame: " + _tail(shot.end_state))
        paragraphs.append(" ".join(p for p in parts if p))
        total += shot.duration_seconds

    if segment.voiceover.strip() and narration_mode == "ltx":
        line = " ".join(segment.voiceover.split()).replace('"', "'")
        hint = f", {voiceover_style.strip()}" if voiceover_style.strip() else ""
        paragraphs.append(f'A narrator\'s voice-over (English{hint}) says: "{line}"')

    style = " ".join(visual_style.split())
    prompt = (style + " " if style else "") + " ".join(paragraphs)
    return prompt.strip(), max(3, min(total, 15))
