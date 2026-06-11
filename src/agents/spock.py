"""Spock — the memory and context curator.

Applies a partial world_state delta and produces a narrow context_brief
for Tolkien's next turn (across the one-turn-delayed feedback loop).

Two modes (config.memory_backend):
- "prose" (legacy): also rolls the full narrative_memory prose forward.
- "mem0": episodic memory lives in the Mem0 store (written post-turn by
  the graph's memory node); Spock keeps only the ledger and the brief.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import (
    call_llm_structured,
    format_characters_full,
    format_locations,
    format_recent_history,
    format_world_state,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import MemoryUpdate, MemoryUpdateLite
from src.state.story_state import StoryState
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("spock.system.md")
USER_PATH: Path = prompt_path("spock.user.md")
SYSTEM_PATH_MEM0: Path = prompt_path("spock.system.mem0.md")
USER_PATH_MEM0: Path = prompt_path("spock.user.mem0.md")


async def run(
    state: StoryState,
    llm: LLMBackend,
    config: Config,
) -> dict:
    if (
        state.current_beat is None
        or state.current_shot is None
        or state.current_commentary is None
    ):
        logger.warning(
            "Spock skipped turn %s — missing beat/shot/commentary",
            state.turn_number,
        )
        return {}

    mem0_mode = config.memory_backend == "mem0"

    shared_user_kwargs = dict(
        turn_number=state.turn_number,
        beat_narration=state.current_beat.narration,
        beat_action=state.current_beat.action,
        beat_outcome=state.current_beat.outcome,
        shot_i2v_prompt=state.current_shot.i2v_prompt,
        shot_on_screen=", ".join(state.current_shot.on_screen) or "(none listed)",
        shot_end_frame_description=state.current_shot.end_frame_description,
        commentary_voiceover=state.current_commentary.voiceover,
        world_state=format_world_state(state.world_state),
        recent_history=format_recent_history(state, count=config.context_window_history),
    )

    if mem0_mode:
        system_prompt = load_prompt(
            SYSTEM_PATH_MEM0,
            locations=format_locations(state),
            characters=format_characters_full(state),
        )
        user_prompt = load_prompt(USER_PATH_MEM0, **shared_user_kwargs)
        schema: type = MemoryUpdateLite
    else:
        system_prompt = load_prompt(
            SYSTEM_PATH,
            narrative_memory_target_tokens=config.narrative_memory_target_tokens,
            locations=format_locations(state),
            characters=format_characters_full(state),
        )
        user_prompt = load_prompt(
            USER_PATH,
            narrative_memory=state.narrative_memory or "(no memory yet)",
            **shared_user_kwargs,
        )
        schema = MemoryUpdate

    # strict=False: WorldStateDelta.characters is a free-form dict[str, dict],
    # which OpenAI's strict subset can't express (additionalProperties must be
    # false). Non-strict still guarantees valid JSON; Pydantic validates below.
    parsed = await call_llm_structured(
        agent="spock",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=state.turn_number,
        schema=schema,
        strict=False,
    )

    if parsed is None:
        return {}

    try:
        update = schema(**parsed)
    except ValidationError as exc:
        logger.warning(
            "Spock memory update validation failed on turn %s: %s",
            state.turn_number, exc,
        )
        return {}

    # Merge the delta into a copy of the current world_state — graph
    # merges the returned dict back into state wholesale.
    new_world_state = _apply_delta(state.world_state, update.world_state_delta)

    result = {
        "world_state": new_world_state,
        "context_brief": update.context_brief,
    }
    if not mem0_mode:
        result["narrative_memory"] = update.narrative_memory
    return result


def _apply_delta(world_state: dict, delta) -> dict:
    """Pure-function variant of StoryState.apply_world_delta."""
    new_ws = dict(world_state)  # shallow copy; characters dict is copied below

    if delta.protagonist_location:
        new_ws["protagonist_location"] = delta.protagonist_location

    if delta.inventory is not None:
        new_ws["inventory"] = list(delta.inventory)

    if delta.characters:
        existing = dict(new_ws.get("characters", {}))
        for name, updates in delta.characters.items():
            if not isinstance(updates, dict):
                continue
            existing[name] = {**existing.get(name, {}), **updates}
        new_ws["characters"] = existing

    return new_ws
