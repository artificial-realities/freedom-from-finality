"""Spielberg — the shot composer.

Reads visual_style, full locations/characters, current_beat, previous
end_frame_description, and current world_state (protagonist_location,
inventory). Writes `current_shot`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import (
    call_llm_structured,
    format_characters_full,
    format_inventory,
    format_locations,
    format_world_state_other,
    previous_end_frame,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Shot
from src.state.story_state import StoryState
from src.util.interaction_logger import InteractionLogger
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("spielberg.system.md")
USER_PATH: Path = prompt_path("spielberg.user.md")


def _default_location(state: StoryState) -> str:
    current = state.world_state.get("protagonist_location")
    if current:
        return current
    if state.locations:
        return state.locations[0].name
    return "(unset)"


async def run(
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    interaction_logger: InteractionLogger,
) -> dict:
    if state.current_beat is None:
        logger.warning("Spielberg skipped turn %s — no current_beat", state.turn_number)
        return {}

    system_prompt = load_prompt(
        SYSTEM_PATH,
        visual_style=state.visual_style,
        locations=format_locations(state),
        characters=format_characters_full(state),
    )
    user_prompt = load_prompt(
        USER_PATH,
        turn_number=state.turn_number,
        beat_narration=state.current_beat.narration,
        beat_action=state.current_beat.action,
        beat_outcome=state.current_beat.outcome,
        previous_end_frame_description=previous_end_frame(state) or "(turn 1 — no previous frame)",
        protagonist_location=_default_location(state),
        inventory=format_inventory(state.world_state),
        world_state_other=format_world_state_other(state.world_state),
    )

    parsed = await call_llm_structured(
        agent="spielberg",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        logger_obj=interaction_logger,
        turn=state.turn_number,
    )

    if parsed is None:
        return {}

    try:
        shot = Shot(**parsed)
    except ValidationError as exc:
        logger.warning("Spielberg shot validation failed on turn %s: %s", state.turn_number, exc)
        return {}

    return {"current_shot": shot}
