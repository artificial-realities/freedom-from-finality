"""Helpers shared by every agent: LLM call + logging + JSON parsing,
and a few formatters that render state into prompt-ready strings.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.llm.base import LLMBackend
from src.models.config import Config
from src.state.story_state import StoryState
from src.util.interaction_logger import InteractionLogger
from src.util.json_sanitizer import parse_structured_response, safe_json_dumps

logger = logging.getLogger(__name__)


async def call_llm_structured(
    *,
    agent: str,
    system_prompt: str,
    user_prompt: str,
    llm: LLMBackend,
    config: Config,
    logger_obj: InteractionLogger,
    turn: int,
    max_tokens: int | None = None,
) -> dict | None:
    """Call the LLM, log it, and return a parsed dict (or None on failure).

    Parse failures return None; callers decide whether to skip the update.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    start = time.monotonic()
    raw, usage = await llm.generate(
        messages=messages,
        temperature=config.temperature,
        max_tokens=max_tokens or config.max_tokens_per_agent,
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    parsed = parse_structured_response(raw)

    logger_obj.log_llm_call(
        agent=agent,
        turn=turn,
        model=llm.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        raw_response=raw,
        parsed_response=parsed,
        token_usage=usage,
        latency_ms=latency_ms,
        temperature=config.temperature,
        max_tokens=max_tokens or config.max_tokens_per_agent,
    )

    if parsed is None:
        logger.warning(
            "agent=%s turn=%s: structured parse failed — skipping update",
            agent, turn,
        )
    return parsed


def format_world_state(world_state: dict) -> str:
    if not world_state:
        return "(empty — this is the start of the story)"
    return safe_json_dumps(world_state, indent=2)


def format_inventory(world_state: dict) -> str:
    items = world_state.get("inventory")
    if not items:
        return "(empty)"
    return ", ".join(items)


def format_world_state_other(world_state: dict) -> str:
    """Everything in world_state that isn't `protagonist_location` or `inventory`."""
    filtered = {
        k: v for k, v in world_state.items()
        if k not in {"protagonist_location", "inventory"}
    }
    if not filtered:
        return "(nothing else tracked yet)"
    return safe_json_dumps(filtered, indent=2)


def format_recent_narration(state: StoryState, count: int = 3) -> str:
    recent = state.get_recent_history(count)
    if not recent:
        return "(no prior beats yet)"
    return "\n\n".join(
        f"[turn {h.turn}] {h.beat.narration}" for h in recent
    )


def format_recent_commentary(state: StoryState, count: int = 5) -> str:
    recent = state.get_recent_history(count)
    if not recent:
        return "(no prior commentary yet)"
    return "\n\n".join(
        f"[turn {h.turn}] {h.commentary.voiceover}" for h in recent
    )


def format_recent_history(state: StoryState, count: int = 5) -> str:
    recent = state.get_recent_history(count)
    if not recent:
        return "(no prior turns yet)"
    chunks = []
    for h in recent:
        chunks.append(
            f"[turn {h.turn}] user_input: {h.user_input!r}\n"
            f"  beat.narration: {h.beat.narration}\n"
            f"  beat.action: {h.beat.action}\n"
            f"  beat.outcome: {h.beat.outcome}\n"
            f"  shot.end_frame: {h.shot.end_frame_description}\n"
            f"  commentary: {h.commentary.voiceover}"
        )
    return "\n\n".join(chunks)


def format_locations(state: StoryState) -> str:
    if not state.locations:
        return "(none)"
    return "\n\n".join(
        f"- {loc.name}: {loc.description}" for loc in state.locations
    )


def format_characters_full(state: StoryState) -> str:
    if not state.characters:
        return "(none)"
    return "\n\n".join(
        f"- {c.name}: {c.description}" for c in state.characters
    )


def format_protagonist(state: StoryState) -> str:
    if not state.characters:
        return "(no protagonist defined)"
    p = state.characters[0]
    return f"{p.name}: {p.description}"


def format_world_constraints(state: StoryState) -> str:
    if not state.world_constraints:
        return "(none)"
    return "\n".join(f"- {c}" for c in state.world_constraints)


def previous_end_frame(state: StoryState) -> str:
    if not state.history:
        return ""
    return state.history[-1].shot.end_frame_description


def format_list_or_empty(items: list[Any] | None) -> str:
    if not items:
        return "(none)"
    return ", ".join(str(x) for x in items)
