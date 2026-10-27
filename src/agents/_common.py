"""Shared LLM call helper: one strict structured-output call, parsed."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from src.llm.base import LLMBackend

logger = logging.getLogger(__name__)


async def call_llm_structured(
    *,
    agent: str,
    system_prompt: str,
    user_prompt: str,
    llm: LLMBackend,
    clip_no: int,
    schema: type[BaseModel],
    max_tokens: int,
) -> tuple[dict | None, str]:
    """Call the LLM constrained to `schema` and return the parsed dict and
    "", or None and why there is none: the call failed, the model refused,
    the reply was cut off at `max_tokens`, or its text is not a JSON
    object. Callers decide what to do without it."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw, info = await llm.generate_structured(messages=messages, schema=schema, max_tokens=max_tokens)
    if info.get("error"):
        why = info["error"]
    elif info.get("refusal"):
        why = f"the model refused: {str(info['refusal'])[:160]}"
    elif info.get("finish_reason") == "length":
        why = f"the reply was cut off at {max_tokens} tokens"
    else:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed, ""
        why = "the reply was not a JSON object"
    logger.warning("agent=%s clip=%s: no structured reply (%s)", agent, clip_no, why)
    return None, why
