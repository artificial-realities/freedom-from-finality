"""Langfuse tracing — runner-side hooks.

Activated when `LANGFUSE_PUBLIC_KEY` is present in the environment. With
keys missing, every helper here is a silent no-op so benchmark/play/live
runs work identically with or without tracing.

The actual capture of prompts/completions/tokens/latency happens via the
`langfuse.openai` drop-in wrapper in `src/llm/openai_backend.py`. This
module only adds the surrounding structure: a per-turn span that groups
the agent generations, plus a flush() at run end so short processes
don't lose buffered events.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Iterator

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True only when a public key is configured.

    The OpenAI wrapper itself stays imported either way — it falls back
    to a pass-through when credentials are missing — but we skip the
    span/flush plumbing to keep no-key runs zero-overhead.
    """
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY"))


@contextlib.contextmanager
def turn_span(
    *,
    turn_number: int,
    session_id: str,
    tags: list[str],
    metadata: dict | None = None,
) -> Iterator[None]:
    """Wrap one turn so all child OpenAI calls become children of one trace.

    Parameters
    ----------
    turn_number:
        1-indexed turn counter; becomes the span name (`turn_<n>`).
    session_id:
        Stable identifier for the entire run, so the Langfuse "Sessions"
        view groups every turn of one play/scenario together.
    tags:
        Free-form filter labels (e.g., `[config.name, "scenario", stem]`).
    metadata:
        Extra fields to attach to the span itself (turn-level info that
        isn't a tag — e.g., `{"config": "mas", "user_input": "..."}`).
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
                yield
    except Exception:
        # A tracing failure must never break the run.
        logger.exception("Langfuse turn_span failed; continuing without span.")
        yield


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
