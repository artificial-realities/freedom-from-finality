"""Abstract LLM backend interface."""

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
        *,
        trace_name: str | None = None,
        trace_metadata: dict | None = None,
    ) -> tuple[str, dict]:
        """Return (response_text, token_usage).

        `trace_name` and `trace_metadata` are optional observability
        labels (e.g., agent name, turn number). Backends that don't
        trace simply ignore them.
        """
        ...
