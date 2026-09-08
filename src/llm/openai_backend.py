"""OpenAI chat completions backend (GPT-4o et al.).

Imports the OpenAI client through the `langfuse.openai` drop-in wrapper.
With LANGFUSE_PUBLIC_KEY set, every call is captured as a Langfuse
generation with model, prompts, completions, tokens, latency, and cost.
With keys absent the wrapper falls back to a pure pass-through and
behaves exactly like the upstream `openai` SDK.
"""

from __future__ import annotations

import logging
import os

from langfuse.openai import AsyncOpenAI
# Private SDK helper, but it is the exact transform the SDK's own
# `chat.completions.parse()` uses internally. We call `create()` instead
# of `parse()` because `parse()` forces strict mode, which free-form-dict
# schemas can't satisfy.
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

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
        *,
        trace_name: str | None = None,
        trace_metadata: dict | None = None,
    ) -> tuple[str, dict]:
        # `name` and `metadata` are extra kwargs the langfuse.openai wrapper
        # consumes to label the generation; they're stripped before the
        # underlying OpenAI call. Only forward when set so a non-Langfuse
        # client wouldn't choke on unknown kwargs.
        extra: dict = {}
        if trace_name is not None:
            extra["name"] = trace_name
        if trace_metadata is not None:
            extra["metadata"] = trace_metadata

        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **extra,
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
        """Generate with native structured outputs.

        `strict=True` enforces the schema at the API level (malformed or
        non-conforming JSON becomes impossible). `strict=False` still
        guarantees syntactically valid JSON with the schema as guidance —
        used for schemas the strict subset can't express (free-form dicts).
        """
        extra: dict = {}
        if trace_name is not None:
            extra["name"] = trace_name
        if trace_metadata is not None:
            extra["metadata"] = trace_metadata

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": strict,
                "schema": (
                    to_strict_json_schema(schema)
                    if strict
                    else schema.model_json_schema()
                ),
            },
        }

        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                **extra,
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
