"""Anthropic chat backend — used by the judge, never by the generator.

Kept deliberately minimal: the judge does not need the structured-prompt
helpers, observability hooks, or token-budget knobs the generator path
uses. Prompt caching is enabled on the system prompt so per-turn judging
across a 180-turn run reuses the rubric instead of re-paying for it 180
times.

The judge is intentionally NOT routed through the langfuse drop-in — its
calls are post-hoc evaluation activity, separate from the run traces it
is scoring. Mixing them would muddy the operational dashboards.
"""

from __future__ import annotations

import logging
import os

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "claude-haiku-4-5-20251001"


class AnthropicBackend:
    """Tiny wrapper around `anthropic.AsyncAnthropic` for the judge."""

    def __init__(self, model: str = DEFAULT_JUDGE_MODEL, api_key: str | None = None) -> None:
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    async def generate(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """Return the assistant's text content. Empty string on failure.

        The system prompt is sent with `cache_control` so Anthropic's
        prompt cache reuses it across calls — a 5-minute TTL is plenty
        for a benchmark scoring pass that fans out concurrently.
        """
        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user}],
            )
        except Exception:
            logger.exception("Anthropic backend call failed")
            return ""

        if not resp.content:
            return ""
        # Messages API returns a list of content blocks; first text block is what we want.
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text or ""
        return ""
