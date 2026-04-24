"""Tolkien — the beat writer.

Reads premise, constraints, long/short narrative, protagonist, current
world_state, narrative_memory, last ~3 beats' narration, Spock's brief
from the previous turn, and user_input. Writes `current_beat` + updated
narrative direction.

On turn 1 the brief, memory, and history are empty; Tolkien opens from
blueprint material alone.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import (
    call_llm_structured,
    format_protagonist,
    format_recent_narration,
    format_world_constraints,
    format_world_state,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Beat
from src.state.story_state import StoryState
from src.util.interaction_logger import InteractionLogger
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("tolkien.system.md")
USER_PATH: Path = prompt_path("tolkien.user.md")


async def run(
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    interaction_logger: InteractionLogger,
) -> dict:
    system_prompt = load_prompt(
        SYSTEM_PATH,
        world_constraints=format_world_constraints(state),
        protagonist=format_protagonist(state),
        narrative_premise=state.narrative_premise,
    )
    user_prompt = load_prompt(
        USER_PATH,
        turn_number=state.turn_number,
        long_term_narrative=state.long_term_narrative or "(not yet set)",
        short_term_narrative=state.short_term_narrative or "(not yet set)",
        world_state=format_world_state(state.world_state),
        narrative_memory=state.narrative_memory or "(no memory yet — this is early in the story)",
        recent_narration=format_recent_narration(state, count=3),
        context_brief=state.context_brief or "(empty — this is turn 1 or the brief is blank)",
        user_input=state.user_input or "(empty — advance on short_term_narrative)",
    )

    parsed = await call_llm_structured(
        agent="tolkien",
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
        beat = Beat(**parsed)
    except ValidationError as exc:
        logger.warning("Tolkien beat validation failed on turn %s: %s", state.turn_number, exc)
        return {}

    update: dict = {
        "current_beat": beat,
        "short_term_narrative": beat.short_term_narrative or state.short_term_narrative,
    }
    if beat.long_term_narrative:
        update["long_term_narrative"] = beat.long_term_narrative
    return update
