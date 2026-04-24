"""OpenAI chat completions backend (GPT-4o et al.)."""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

from src.llm.base import LLMBackend

logger = logging.getLogger(__name__)


class OpenAIBackend(LLMBackend):
    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    async def generate(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> tuple[str, dict]:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            logger.exception("OpenAI backend call failed: %s", exc)
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
