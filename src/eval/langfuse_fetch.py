"""Pull a finished run out of Langfuse for offline scoring.

`fetch_session(session_id)` returns one `TurnRecord` per turn, ordered by
turn number, with everything an LLM judge needs: user_input, parsed
agent outputs (Beat / Shot / Commentary / MemoryUpdate), token usage,
latency, and cost. The Markdown transcript stays the canonical
narrative artifact; this module is for programmatic scoring.

Use `lf.api.trace.get(trace_id)` rather than `observations.get_many` —
the V2 observations endpoint silently drops `model`, `usage`, and
`name` from generation rows. `trace.get` returns the full embedded
observations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GenerationRecord:
    agent: str
    model: str | None
    usage: dict[str, Any]
    latency_s: float | None
    raw_output: str
    parsed_output: dict | None


@dataclass
class TurnRecord:
    turn: int
    trace_id: str
    user_input: str
    config: str
    tags: list[str]
    latency_s: float | None
    total_cost: float | None
    generations: list[GenerationRecord] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def _parse_turn_number(trace_name: str | None) -> int:
    """Trace name is `turn_<n>` per `langfuse_setup.turn_span`."""
    if not trace_name or not trace_name.startswith("turn_"):
        return 0
    try:
        return int(trace_name.removeprefix("turn_"))
    except ValueError:
        return 0


def _extract_assistant_content(output: Any) -> str:
    """OpenAI generations come back as `{"role": "assistant", "content": "..."}`.

    Older runs may serialise it differently; we accept dict, str, or None.
    """
    if output is None:
        return ""
    if isinstance(output, dict):
        return output.get("content", "") or ""
    if isinstance(output, str):
        return output
    return str(output)


def _try_parse_json(s: str) -> dict | None:
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def fetch_session(session_id: str) -> list[TurnRecord]:
    """Pull every trace for `session_id` and flatten into per-turn records.

    Caller must have `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
    `LANGFUSE_HOST` exported (e.g. via `dotenv.load_dotenv()` upstream).
    """
    from langfuse import Langfuse  # local import — keeps non-eval imports light

    lf = Langfuse()
    sess = lf.api.sessions.get(session_id)
    records: list[TurnRecord] = []

    for trace_stub in sess.traces:
        trace = lf.api.trace.get(trace_stub.id)
        meta = trace.model_dump().get("metadata") or {}
        user_input = meta.get("user_input", "")
        config_name = meta.get("config", "")

        record = TurnRecord(
            turn=_parse_turn_number(trace.name),
            trace_id=trace.id,
            user_input=user_input,
            config=config_name,
            tags=list(trace.tags or []),
            latency_s=trace.latency,
            total_cost=trace.total_cost,
        )

        for o in trace.observations:
            d = o.model_dump() if hasattr(o, "model_dump") else dict(o)
            otype = d.get("type")
            obs_meta = d.get("metadata") or {}

            if otype == "GENERATION":
                raw = _extract_assistant_content(d.get("output"))
                record.generations.append(GenerationRecord(
                    agent=obs_meta.get("agent") or d.get("name") or "",
                    model=d.get("model"),
                    usage=d.get("usage") or {},
                    latency_s=d.get("latency"),
                    raw_output=raw,
                    parsed_output=_try_parse_json(raw),
                ))
            elif otype == "EVENT":
                record.events.append({
                    "name": d.get("name"),
                    "level": d.get("level"),
                    "metadata": {k: v for k, v in obs_meta.items()
                                 if not (k.startswith("scope.")
                                         or k.startswith("resourceAttributes"))},
                    "input": d.get("input"),
                    "output": d.get("output"),
                })
            # SPAN observations are the turn span itself — already represented by `record`.

        records.append(record)

    records.sort(key=lambda r: r.turn)
    return records


def turn_record_for_judge(record: TurnRecord) -> dict:
    """Reduce a TurnRecord to the shape an LLM judge prompt wants.

    Keeps only what's narratively meaningful: the user's command and the
    parsed agent outputs keyed by agent name. Drop tokens/latency/cost —
    those go in a separate operational summary, not the judge prompt.
    """
    by_agent = {g.agent: g.parsed_output for g in record.generations if g.agent}
    return {
        "turn": record.turn,
        "user_input": record.user_input,
        "outputs": by_agent,
    }
