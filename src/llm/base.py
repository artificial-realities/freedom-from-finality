"""Abstract LLM backend — Gemma (vLLM) and OpenAI share this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMBackend(ABC):
    model: str

    @abstractmethod
    async def generate(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> tuple[str, dict]:
        """Return (response_text, token_usage)."""
        ...
