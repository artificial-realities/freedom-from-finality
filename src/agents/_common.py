"""Shared LLM call helper: native structured outputs + parse fallback."""

from __future__ import annotations

import logging

from pydantic import BaseModel

from src.llm.base import LLMBackend
from src.models.config import Config
from src.util.json_sanitizer import parse_structured_response

logger = logging.getLogger(__name__)


async def call_llm_structured(
    *,
    agent: str,
    system_prompt: str,
    user_prompt: str,
    llm: LLMBackend,
    config: Config,
    turn: int,
    schema: type[BaseModel],
    strict: bool = True,
    max_tokens: int | None = None,
) -> dict | None:
    """Call the LLM with native structured outputs and return a parsed dict.

    `schema` constrains the response at the API level (`strict=True`
    enforces it; `strict=False` guarantees valid JSON with the schema as
    guidance). Prompts, completions, tokens and latency are captured by
    the `langfuse.openai` wrapper inside the backend. Parse failures
    return None; callers decide whether to skip the update.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    raw, _usage = await llm.generate_structured(
        messages=messages,
        schema=schema,
        strict=strict,
        temperature=config.temperature,
        max_tokens=max_tokens or config.max_tokens_per_agent,
        trace_name=agent,
        trace_metadata={"turn": turn, "config": config.name, "agent": agent},
    )

    parsed = parse_structured_response(raw)
    if parsed is None:
        logger.warning(
            "agent=%s turn=%s: structured parse failed — skipping update",
            agent, turn,
        )
    return parsed
