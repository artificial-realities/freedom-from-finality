"""Langfuse tracing — runner-side hooks.

Activated when `LANGFUSE_PUBLIC_KEY` is present in the environment. With
keys missing, every helper here is a silent no-op so benchmark/play/live
runs work identically with or without tracing.

The actual capture of prompts/completions/tokens/latency happens via the
`langfuse.openai` drop-in wrapper in `src/llm/openai_backend.py`. This
module adds the surrounding structure: a per-turn span that groups the
agent generations, trace I/O so the Sessions view shows the user_input
and the resulting voiceover, plus discrete events for non-LLM things
(pacing holds, i2v render outcomes, TTS calls, playback transitions).
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True only when a public key is configured.

    The OpenAI wrapper itself stays imported either way — it falls back
    to a pass-through when credentials are missing — but we skip the
    span/event/flush plumbing to keep no-key runs zero-overhead.
    """
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY"))


@contextlib.contextmanager
def turn_span(
    *,
    turn_number: int,
    session_id: str,
    tags: list[str],
    user_input: str,
    metadata: dict | None = None,
) -> Iterator[None]:
    """Wrap one turn so all child OpenAI calls become children of one trace.

    Sets the trace input to `user_input` so the Sessions view shows the
    turn's prompt at the trace card level, instead of falling back to
    the first child generation's input.
    """
    if not is_enabled():
        yield
        return

    try:
        from langfuse import get_client, propagate_attributes
    except ImportError:
        logger.warning(
            "LANGFUSE_PUBLIC_KEY is set but `langfuse` is not installed — "
            "skipping turn span. Run `pip install langfuse` to enable tracing."
        )
        yield
        return

    try:
        client = get_client()
        with client.start_as_current_observation(
            as_type="span",
            name=f"turn_{turn_number}",
            metadata=metadata or {},
        ):
            with propagate_attributes(session_id=session_id, tags=list(tags)):
                try:
                    client.set_current_trace_io(input=user_input)
                except Exception:
                    logger.debug("Could not set trace input", exc_info=True)
                yield
    except Exception:
        # A tracing failure must never break the run.
        logger.exception("Langfuse turn_span failed; continuing without span.")
        yield


def set_trace_output(output: Any) -> None:
    """Update the current trace's output. Call after the graph has run."""
    if not is_enabled():
        return
    try:
        from langfuse import get_client
        get_client().set_current_trace_io(output=output)
    except Exception:
        logger.debug("Langfuse set_trace_output failed.", exc_info=True)


def event(
    name: str,
    *,
    metadata: dict | None = None,
    input: Any = None,
    output: Any = None,
    level: str = "DEFAULT",
) -> None:
    """Emit a discrete event on the current trace.

    For non-LLM things the openai wrapper doesn't already capture: pacing
    holds, i2v render outcomes, TTS calls, playback transitions. When
    called outside an active span, the event still attaches to the
    current trace context if one exists; otherwise it's silently dropped.
    """
    if not is_enabled():
        return
    try:
        from langfuse import get_client
        get_client().create_event(
            name=name,
            input=input,
            output=output,
            metadata=metadata,
            level=level,  # type: ignore[arg-type]
        )
    except Exception:
        logger.debug("Langfuse event %r failed.", name, exc_info=True)


def flush() -> None:
    """Flush buffered events. Call from each runner's finally block.

    Short-lived processes (one-shot benchmarks, scenario runs) will lose
    pending traces without this — the SDK batches in the background.
    """
    if not is_enabled():
        return
    try:
        from langfuse import get_client
    except ImportError:
        return
    try:
        get_client().flush()
    except Exception:
        logger.exception("Langfuse flush failed.")
