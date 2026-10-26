"""Shared LLM call helper: one strict structured-output call, parsed."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from src.llm.base import LLMBackend
from src.models.config import Config

logger = logging.getLogger(__name__)


async def call_llm_structured(
    *,
    agent: str,
    system_prompt: str,
    user_prompt: str,
    llm: LLMBackend,
    config: Config,
    clip_no: int,
    schema: type[BaseModel],
    max_tokens: int,
) -> dict | None:
    """Call the LLM constrained to `schema` and return the parsed dict, or
    None when the call failed or its text is not a JSON object (a truncated
    reply, a refusal); callers decide what to do without it."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw, _usage = await llm.generate_structured(
        messages=messages, schema=schema, temperature=config.temperature, max_tokens=max_tokens,
    )
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        logger.warning("agent=%s clip=%s: no structured reply, skipping update", agent, clip_no)
        return None
    return parsed
