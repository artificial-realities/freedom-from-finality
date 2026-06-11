"""Operational stats for a finished session, mined from Langfuse traces.

The telemetry is all already captured (per-agent cost / latency / tokens
via the `langfuse.openai` wrapper, per-turn spans, discrete events) —
this module is the consumer that turns it into a terminal dashboard:

    python main.py stats <session_id>

Per-agent table (who burns time/money), per-turn cost+latency trend
(does the system get slower/pricier as context grows), and any
WARNING/ERROR events (holds, render failures, skipped updates).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from src.eval.langfuse_fetch import TurnRecord, fetch_session


@dataclass
class AgentStats:
    agent: str
    calls: int = 0
    parse_failures: int = 0
    latency_mean_s: float = 0.0
    latency_p95_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class TurnStats:
    turn: int
    latency_s: float | None
    cost_usd: float | None
    user_input: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class SessionStats:
    session_id: str
    config: str
    n_turns: int
    total_cost_usd: float
    total_latency_s: float
    agents: list[AgentStats] = field(default_factory=list)
    turns: list[TurnStats] = field(default_factory=list)
    warning_events: list[dict] = field(default_factory=list)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return ordered[idx]


def compute_stats(session_id: str, records: list[TurnRecord]) -> SessionStats:
    """Pure aggregation over fetched TurnRecords (testable without Langfuse)."""
    per_agent_latency: dict[str, list[float]] = {}
    agents: dict[str, AgentStats] = {}
    turns: list[TurnStats] = []
    warning_events: list[dict] = []

    for r in records:
        turn_warnings: list[str] = []
        for ev in r.events:
            if (ev.get("level") or "").upper() in {"WARNING", "ERROR"}:
                label = ev.get("name") or "(unnamed event)"
                turn_warnings.append(label)
                warning_events.append({"turn": r.turn, "name": label,
                                       "level": ev.get("level")})

        turns.append(TurnStats(
            turn=r.turn,
            latency_s=r.latency_s,
            cost_usd=r.total_cost,
            user_input=r.user_input,
            warnings=turn_warnings,
        ))

        for g in r.generations:
            name = g.agent or "(unknown)"
            a = agents.setdefault(name, AgentStats(agent=name))
            a.calls += 1
            if g.parsed_output is None:
                a.parse_failures += 1
            if g.latency_s is not None:
                per_agent_latency.setdefault(name, []).append(g.latency_s)
            usage = g.usage or {}
            a.input_tokens += int(usage.get("input") or 0)
            a.output_tokens += int(usage.get("output") or 0)
            a.cost_usd += g.cost or 0.0

    for name, a in agents.items():
        lats = per_agent_latency.get(name, [])
        if lats:
            a.latency_mean_s = sum(lats) / len(lats)
            a.latency_p95_s = _p95(lats)

    config = records[0].config if records else ""
    return SessionStats(
        session_id=session_id,
        config=config,
        n_turns=len(records),
        total_cost_usd=sum(t.cost_usd or 0.0 for t in turns),
        total_latency_s=sum(t.latency_s or 0.0 for t in turns),
        agents=sorted(agents.values(), key=lambda a: a.cost_usd, reverse=True),
        turns=turns,
        warning_events=warning_events,
    )


def session_stats(session_id: str) -> SessionStats:
    """Fetch a session from Langfuse and aggregate it."""
    records = fetch_session(session_id)
    return compute_stats(session_id, records)


# Rendering --------------------------------------------------------------------

_TURN_TABLE_MAX = 40  # above this, bucket turns instead of listing each


def _bucket(turns: list[TurnStats], n_buckets: int = 10) -> list[tuple[str, float, float]]:
    """(label, mean latency, mean cost) per contiguous bucket of turns."""
    if not turns:
        return []
    size = max(1, len(turns) // n_buckets)
    out: list[tuple[str, float, float]] = []
    for i in range(0, len(turns), size):
        chunk = turns[i:i + size]
        label = f"{chunk[0].turn}-{chunk[-1].turn}"
        lats = [t.latency_s for t in chunk if t.latency_s is not None]
        costs = [t.cost_usd for t in chunk if t.cost_usd is not None]
        out.append((
            label,
            sum(lats) / len(lats) if lats else 0.0,
            sum(costs) / len(costs) if costs else 0.0,
        ))
    return out


def format_stats(stats: SessionStats) -> str:
    lines: list[str] = []
    lines.append(f"Session: {stats.session_id}")
    lines.append(f"Config:  {stats.config}   Turns: {stats.n_turns}   "
                 f"Total cost: ${stats.total_cost_usd:.4f}   "
                 f"Total LLM latency: {stats.total_latency_s:.1f}s")
    lines.append("")

    lines.append("Per agent (sorted by cost):")
    header = (f"  {'agent':<14}{'calls':>6}{'fails':>6}{'mean s':>8}"
              f"{'p95 s':>8}{'in tok':>9}{'out tok':>9}{'cost $':>9}")
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for a in stats.agents:
        lines.append(
            f"  {a.agent:<14}{a.calls:>6}{a.parse_failures:>6}"
            f"{a.latency_mean_s:>8.2f}{a.latency_p95_s:>8.2f}"
            f"{a.input_tokens:>9}{a.output_tokens:>9}{a.cost_usd:>9.4f}"
        )
    lines.append("")

    if stats.n_turns <= _TURN_TABLE_MAX:
        lines.append("Per turn:")
        lines.append(f"  {'turn':>4}  {'lat s':>7}  {'cost $':>8}  input")
        for t in stats.turns:
            lat = f"{t.latency_s:.1f}" if t.latency_s is not None else "-"
            cost = f"{t.cost_usd:.4f}" if t.cost_usd is not None else "-"
            ui = t.user_input or "(silent)"
            if len(ui) > 48:
                ui = ui[:45] + "..."
            warn = f"  ⚠ {', '.join(t.warnings)}" if t.warnings else ""
            lines.append(f"  {t.turn:>4}  {lat:>7}  {cost:>8}  {ui}{warn}")
    else:
        lines.append("Per turn-bucket (context-growth trend):")
        lines.append(f"  {'turns':>9}  {'mean lat s':>10}  {'mean cost $':>11}")
        for label, lat, cost in _bucket(stats.turns):
            lines.append(f"  {label:>9}  {lat:>10.2f}  {cost:>11.4f}")
    lines.append("")

    if stats.warning_events:
        lines.append(f"Warnings/errors ({len(stats.warning_events)}):")
        for ev in stats.warning_events:
            lines.append(f"  turn {ev['turn']:>4}: {ev['name']} [{ev['level']}]")
    else:
        lines.append("Warnings/errors: none")

    return "\n".join(lines)


def stats_as_dict(stats: SessionStats) -> dict:
    return asdict(stats)
