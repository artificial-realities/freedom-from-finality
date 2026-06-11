"""Abstract LLM backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


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

    async def generate_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        strict: bool = True,
        trace_name: str | None = None,
        trace_metadata: dict | None = None,
    ) -> tuple[str, dict]:
        """Return (response_text, token_usage), constrained to `schema`.

        Backends with native structured-output support override this to
        enforce the schema at the API level (`strict=True`) or guarantee
        syntactically valid JSON guided by it (`strict=False`). This
        default falls back to plain `generate()` — prompt-guided JSON —
        so backends without support keep working unchanged.
        """
        return await self.generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            trace_name=trace_name,
            trace_metadata=trace_metadata,
        )
