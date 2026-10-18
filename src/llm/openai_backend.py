"""OpenAI chat completions backend with strict structured outputs."""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI
# Private SDK helper, but it is the exact transform the SDK's own
# `chat.completions.parse()` uses internally.
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from src.llm.base import LLMBackend

logger = logging.getLogger(__name__)


class OpenAIBackend(LLMBackend):
    def __init__(self, model: str, api_key: str | None = None, reasoning_effort: str = "") -> None:
        self.model = model
        # A reasoning model (the GPT-5 family, o-series) takes its effort
        # and `max_completion_tokens`, and only its default temperature;
        # "" is a chat model (gpt-4.1, gpt-4o) with `max_tokens`.
        self.reasoning_effort = reasoning_effort
        self.client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def _sampling(self, temperature: float, max_tokens: int) -> dict:
        if self.reasoning_effort:
            return {"reasoning_effort": self.reasoning_effort, "max_completion_tokens": max_tokens}
        return {"temperature": temperature, "max_tokens": max_tokens}

    async def generate_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> tuple[str, dict]:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": True, "schema": to_strict_json_schema(schema)},
        }
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **self._sampling(temperature, max_tokens),
                response_format=response_format,
            )
        except Exception as exc:
            logger.exception("OpenAI structured call failed: %s", exc)
            return "", {"error": str(exc)}

        text = resp.choices[0].message.content or ""
        usage = {}
        if resp.usage is not None:
            usage = {
                "prompt": resp.usage.prompt_tokens,
                "completion": resp.usage.completion_tokens,
                "total": resp.usage.total_tokens,
            }
        return text, usage
