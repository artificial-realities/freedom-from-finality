"""Abstract LLM backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMBackend(ABC):
    model: str

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> tuple[str, dict]:
        """Return (response_text, info), the text constrained to `schema`;
        `info` has the token usage, the `finish_reason` and a `refusal` if
        the model refused. A failed call returns ("", {"error": why}),
        never raises."""
        ...
