"""LLM-as-judge harness — scores a finished run and writes scores to Langfuse.

Two scoring layers:

1. **Per-turn local rubric** — four dimensions (anatomy, visual stability,
   commentary coherence, internal coherence) on every turn. Cheap; the
   system prompt is cached so the cost is only the per-turn payload.
2. **Per-window memory rubric** — five dimensions (prop persistence,
   callback quality, bit variety, rule compliance, long-horizon coherence)
   at a small number of probe turns defined in the scenario annotations.
   Each call sees the transcript up to that point.

Every score is pushed to Langfuse via `client.create_score(...)` keyed to
the turn's `trace_id` (per-turn rubric) or the probe turn's `trace_id`
(per-window rubric). Comments cite specific turns / fields so a human
reviewer can sanity-check the judge.

Designed to be idempotent enough for spot-rerunning: scores with the same
(name, trace_id) pair are upserted by Langfuse rather than duplicated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.eval.langfuse_fetch import TurnRecord, fetch_session
from src.llm.anthropic_backend import DEFAULT_JUDGE_MODEL, AnthropicBackend
from src.util.json_sanitizer import parse_structured_response
from src.util.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path("src/prompts")
DEFAULT_ANNOTATIONS = Path("data/test_scenario_annotations.json")
DEFAULT_CONCURRENCY = 8

LOCAL_DIMENSIONS = (
    "rule_compliance",
    "commentary_on_screen",
    "internal_coherence",
)
WINDOW_DIMENSIONS = (
    "prop_persistence",
    "callback_quality",
    "long_horizon_coherence",
)


@dataclass
class JudgeReport:
    session_id: str
    config: str
    judge_model: str
    n_turns: int
    n_local_scored: int
    n_window_scored: int
    aggregates: dict[str, Any]


# Annotation helpers ----------------------------------------------------------

def load_annotations(path: Path | str = DEFAULT_ANNOTATIONS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def phase_for_turn(annotations: dict, turn: int) -> str:
    for ph in annotations.get("phases", []):
        if ph["start"] <= turn <= ph["end"]:
            return ph["name"]
    return "unknown"


def turn_annotation_block(annotations: dict, turn: int) -> str:
    """Annotation context for the per-turn judge prompt.

    Surfaces the specific things the judge should look out for on this
    turn (introduced prop, callback target, trap kind). Kept terse to
    avoid biasing scores beyond the rule the trap is actually testing.
    """
    bits: list[str] = [f"**Phase:** {phase_for_turn(annotations, turn)}"]

    intro = annotations.get("props_introduced", {}).get(str(turn))
    if intro:
        bits.append(f"**Prop introduced this turn:** {intro}")

    cb = annotations.get("explicit_callbacks", {}).get(str(turn))
    if cb:
        refs = cb.get("to") or []
        kind = cb.get("kind", "")
        if refs:
            bits.append(f"**Callback:** {kind} → turn(s) {refs}. {cb.get('note', '')}".rstrip())
        else:
            bits.append(f"**Callback:** {kind}. {cb.get('note', '')}".rstrip())

    trap = annotations.get("traps", {}).get(str(turn))
    if trap:
        bits.append(
            f"**Trap:** {trap.get('kind', '')} — rule `{trap.get('rule', '')}`. "
            f"{trap.get('description', '')}"
        )

    return "\n\n".join(bits)


def probe_annotation_block(annotations: dict, probe: dict) -> str:
    """Annotation context for the per-window judge prompt."""
    bits: list[str] = [f"**Probe name:** `{probe['name']}`"]
    score_at = probe["score_at_turn"]
    relevant_intros: list[str] = []
    for k, v in annotations.get("props_introduced", {}).items():
        if int(k) <= score_at:
            relevant_intros.append(f"  - turn {k}: {v}")
    if relevant_intros:
        bits.append("**Props introduced in this window:**\n" + "\n".join(relevant_intros))

    relevant_traps: list[str] = []
    for k, v in annotations.get("traps", {}).items():
        if probe["window_start"] <= int(k) <= score_at:
            relevant_traps.append(
                f"  - turn {k}: `{v.get('kind', '')}` ({v.get('rule', '')}) — {v.get('description', '')}"
            )
    if relevant_traps:
        bits.append("**Traps within this window:**\n" + "\n".join(relevant_traps))

    return "\n\n".join(bits)


# Transcript formatting -------------------------------------------------------

def _resolve_outputs(record: TurnRecord) -> dict[str, dict]:
    """Normalize MAS- and solo-shaped runs into one logical layout.

    MAS records four generations (`tolkien` → Beat dict, `spielberg` → Shot
    dict, etc.); solo records one (`solo` → SoloResponse dict containing
    {beat, shot, commentary, memory_update}). Both reduce to:

        {"beat": {...}, "shot": {...}, "commentary": {...}, "memory": {...}}
    """
    by_agent = {g.agent: g.parsed_output or {} for g in record.generations if g.agent}

    if "solo" in by_agent and isinstance(by_agent["solo"], dict):
        s = by_agent["solo"]
        return {
            "beat":       s.get("beat") or {},
            "shot":       s.get("shot") or {},
            "commentary": s.get("commentary") or {},
            "memory":     s.get("memory_update") or {},
        }

    return {
        "beat":       by_agent.get("tolkien") or {},
        "shot":       by_agent.get("spielberg") or {},
        "commentary": by_agent.get("attenborough") or {},
        "memory":     by_agent.get("spock") or {},
    }


def _summarize_turn(record: TurnRecord) -> str:
    """One readable block per turn: user input, beat, shot, voiceover, memory."""
    parts = _resolve_outputs(record)
    beat = parts["beat"]
    shot = parts["shot"]
    commentary = parts["commentary"]
    memory = parts["memory"]

    user_label = f'"{record.user_input}"' if record.user_input.strip() else "(silent)"
    lines = [f"### Turn {record.turn} — {user_label}"]

    if isinstance(beat, dict) and beat.get("narration"):
        lines.append(f"**Beat:** {beat['narration']}")
        if beat.get("action"):
            lines.append(f"_Action:_ {beat['action']}")
        if beat.get("outcome"):
            lines.append(f"_Outcome:_ {beat['outcome']}")
    if isinstance(shot, dict):
        if shot.get("i2v_prompt"):
            lines.append(f"**Shot:** {shot['i2v_prompt']}")
        if shot.get("end_frame_description"):
            lines.append(f"_End frame:_ {shot['end_frame_description']}")
    if isinstance(commentary, dict):
        vo = (commentary.get("voiceover") or "").strip()
        lines.append(f"**Voiceover:** {'(silent)' if not vo else vo}")
    if isinstance(memory, dict):
        nm = (memory.get("narrative_memory") or "").strip()
        cb = (memory.get("context_brief") or "").strip()
        if nm:
            lines.append(f"_Narrative memory:_ {nm}")
        if cb:
            lines.append(f"_Context brief (next turn):_ {cb}")
    return "\n\n".join(lines)


def format_transcript(records: list[TurnRecord], start: int, end: int) -> str:
    """Compact human-readable transcript for the per-window judge prompt."""
    in_window = [r for r in records if start <= r.turn <= end]
    return "\n\n---\n\n".join(_summarize_turn(r) for r in in_window)


# LLM scoring -----------------------------------------------------------------

def _validate_rubric_scores(parsed: dict | None, dimensions: tuple[str, ...]) -> dict | None:
    """Coerce the judge's JSON into a clean {dim: {score, comment}} shape."""
    if not isinstance(parsed, dict):
        return None
    out: dict[str, dict] = {}
    for dim in dimensions:
        entry = parsed.get(dim)
        if not isinstance(entry, dict):
            return None
        score = entry.get("score")
        if not isinstance(score, int) or not 0 <= score <= 3:
            try:
                score_int = int(score)  # type: ignore[arg-type]
                if not 0 <= score_int <= 3:
                    return None
                score = score_int
            except (TypeError, ValueError):
                return None
        comment = (entry.get("comment") or "").strip()
        out[dim] = {"score": score, "comment": comment}
    return out


async def score_turn_local(
    *,
    record: TurnRecord,
    annotations: dict,
    judge: AnthropicBackend,
    system_prompt: str,
    user_template: str,
) -> dict | None:
    """Run the per-turn rubric. Returns parsed-and-validated dict or None.

    The payload sent to the judge is the *normalized* `{beat, shot,
    commentary, memory}` shape (resolved from both MAS and solo runs)
    so the judge's prompt template is config-agnostic.
    """
    normalized = _resolve_outputs(record)
    user_prompt = user_template.format(
        turn_number=record.turn,
        config_name=record.config or "(unknown)",
        user_input=record.user_input or "(silent)",
        annotation_block=turn_annotation_block(annotations, record.turn) or "(no annotation)",
        turn_outputs_json=json.dumps(normalized, indent=2, ensure_ascii=False),
    )
    raw = await judge.generate(system=system_prompt, user=user_prompt, max_tokens=600)
    parsed = parse_structured_response(raw)
    return _validate_rubric_scores(parsed, LOCAL_DIMENSIONS)


async def score_window(
    *,
    probe: dict,
    records: list[TurnRecord],
    annotations: dict,
    judge: AnthropicBackend,
    system_prompt: str,
    user_template: str,
) -> dict | None:
    """Run the per-window memory rubric for one probe."""
    transcript = format_transcript(records, probe["window_start"], probe["score_at_turn"])
    user_prompt = user_template.format(
        probe_name=probe["name"],
        config_name=records[0].config if records else "(unknown)",
        window_start=probe["window_start"],
        score_at_turn=probe["score_at_turn"],
        probe_asks=probe.get("asks", ""),
        annotation_block=probe_annotation_block(annotations, probe) or "(no annotation)",
        transcript=transcript,
    )
    raw = await judge.generate(system=system_prompt, user=user_prompt, max_tokens=900)
    parsed = parse_structured_response(raw)
    return _validate_rubric_scores(parsed, WINDOW_DIMENSIONS)


# Score pushing ---------------------------------------------------------------

def _push_score(
    client,
    *,
    name: str,
    value: float,
    session_id: str,
    trace_id: str | None,
    comment: str = "",
    metadata: dict | None = None,
) -> None:
    """Wrap `client.create_score` with the right kwargs and never raise."""
    if client is None:
        return
    try:
        if trace_id:
            client.create_score(
                name=name,
                value=value,
                data_type="NUMERIC",
                trace_id=trace_id,
                comment=(comment or "")[:500],
                metadata=metadata or {},
            )
        else:
            client.create_score(
                name=name,
                value=value,
                data_type="NUMERIC",
                session_id=session_id,
                comment=(comment or "")[:500],
                metadata=metadata or {},
            )
    except Exception:
        logger.debug("Langfuse create_score(%r) failed.", name, exc_info=True)


def _ingest_local_scores(
    client,
    *,
    session_id: str,
    record: TurnRecord,
    rubric: dict,
    phase: str,
) -> None:
    for dim, entry in rubric.items():
        _push_score(
            client,
            name=f"local.{dim}",
            value=float(entry["score"]),
            session_id=session_id,
            trace_id=record.trace_id,
            comment=entry["comment"],
            metadata={"phase": phase, "turn": record.turn, "rubric": "local"},
        )


def _ingest_window_scores(
    client,
    *,
    session_id: str,
    probe: dict,
    probe_record: TurnRecord | None,
    rubric: dict,
) -> None:
    trace_id = probe_record.trace_id if probe_record else None
    for dim, entry in rubric.items():
        _push_score(
            client,
            name=f"window.{dim}",
            value=float(entry["score"]),
            session_id=session_id,
            trace_id=trace_id,
            comment=entry["comment"],
            metadata={
                "probe_name": probe["name"],
                "score_at_turn": probe["score_at_turn"],
                "window_start": probe["window_start"],
                "rubric": "window",
            },
        )


# Aggregation -----------------------------------------------------------------

def _aggregate(
    *,
    annotations: dict,
    records: list[TurnRecord],
    local_results: dict[int, dict | None],
    window_results: list[tuple[dict, dict | None]],
) -> dict[str, Any]:
    """Mean scores grouped by phase + overall."""
    by_phase: dict[str, dict[str, list[float]]] = {}

    for r in records:
        phase = phase_for_turn(annotations, r.turn)
        bucket = by_phase.setdefault(phase, {})

        rubric = local_results.get(r.turn)
        if rubric:
            for dim, entry in rubric.items():
                bucket.setdefault(f"local.{dim}", []).append(float(entry["score"]))

    phase_means: dict[str, dict[str, float]] = {}
    for phase, dims in by_phase.items():
        phase_means[phase] = {k: round(sum(v) / len(v), 3) for k, v in dims.items() if v}

    window_means: dict[str, float] = {}
    window_count = sum(1 for _, r in window_results if r)
    if window_count:
        all_dims: dict[str, list[float]] = {}
        for _probe, rubric in window_results:
            if not rubric:
                continue
            for dim, entry in rubric.items():
                all_dims.setdefault(dim, []).append(float(entry["score"]))
        window_means = {k: round(sum(v) / len(v), 3) for k, v in all_dims.items()}

    return {
        "phase_means": phase_means,
        "window_means": window_means,
        "n_window_probes_scored": window_count,
    }


# Orchestrator ----------------------------------------------------------------

def _load_judge_prompts() -> tuple[str, str, str, str]:
    return (
        load_prompt(PROMPTS_DIR / "judge.local.system.md"),
        load_prompt(PROMPTS_DIR / "judge.local.user.md"),
        load_prompt(PROMPTS_DIR / "judge.window.system.md"),
        load_prompt(PROMPTS_DIR / "judge.window.user.md"),
    )


def _get_langfuse_client():
    """Return a Langfuse client, or None if keys aren't configured."""
    import os
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        logger.warning("LANGFUSE_PUBLIC_KEY not set — scores will be computed but not uploaded.")
        return None
    try:
        from langfuse import get_client
        return get_client()
    except Exception:
        logger.exception("Could not construct a Langfuse client.")
        return None


async def judge_session(
    session_id: str,
    *,
    annotations_path: Path | str = DEFAULT_ANNOTATIONS,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    concurrency: int = DEFAULT_CONCURRENCY,
    output_dir: Path | str = "logs/judge",
) -> JudgeReport:
    """Score one finished run and write everything to Langfuse + a JSON summary."""
    annotations = load_annotations(annotations_path)
    records = fetch_session(session_id)
    if not records:
        raise RuntimeError(f"No traces found for session {session_id!r}.")

    config = records[0].config or "(unknown)"
    logger.info(
        "Judging session=%s config=%s turns=%d model=%s",
        session_id, config, len(records), judge_model,
    )

    local_sys, local_user, window_sys, window_user = _load_judge_prompts()
    judge = AnthropicBackend(model=judge_model)
    client = _get_langfuse_client()

    # 1. Per-turn local rubric — bounded concurrency.
    sem = asyncio.Semaphore(concurrency)

    async def _local_one(record: TurnRecord) -> tuple[int, dict | None]:
        async with sem:
            try:
                rubric = await score_turn_local(
                    record=record,
                    annotations=annotations,
                    judge=judge,
                    system_prompt=local_sys,
                    user_template=local_user,
                )
            except Exception:
                logger.exception("Local scoring failed for turn %s", record.turn)
                rubric = None
            return record.turn, rubric

    local_pairs = await asyncio.gather(*(_local_one(r) for r in records))
    local_results: dict[int, dict | None] = dict(local_pairs)

    for r in records:
        rubric = local_results.get(r.turn)
        if rubric:
            _ingest_local_scores(
                client,
                session_id=session_id,
                record=r,
                rubric=rubric,
                phase=phase_for_turn(annotations, r.turn),
            )

    # 2. Per-window rubric — small N, run concurrently.
    by_turn: dict[int, TurnRecord] = {r.turn: r for r in records}
    probes = annotations.get("probe_windows", [])

    async def _window_one(probe: dict) -> tuple[dict, dict | None]:
        try:
            rubric = await score_window(
                probe=probe,
                records=records,
                annotations=annotations,
                judge=judge,
                system_prompt=window_sys,
                user_template=window_user,
            )
        except Exception:
            logger.exception("Window scoring failed for probe %s", probe.get("name"))
            rubric = None
        return probe, rubric

    window_pairs = await asyncio.gather(*(_window_one(p) for p in probes))
    window_results: list[tuple[dict, dict | None]] = list(window_pairs)

    for probe, rubric in window_results:
        if rubric:
            _ingest_window_scores(
                client,
                session_id=session_id,
                probe=probe,
                probe_record=by_turn.get(probe["score_at_turn"]),
                rubric=rubric,
            )

    # 3. Flush + persist a JSON summary alongside the run logs.
    if client is not None:
        try:
            client.flush()
        except Exception:
            logger.debug("Langfuse flush failed at end of judge.", exc_info=True)

    aggregates = _aggregate(
        annotations=annotations,
        records=records,
        local_results=local_results,
        window_results=window_results,
    )

    report = JudgeReport(
        session_id=session_id,
        config=config,
        judge_model=judge_model,
        n_turns=len(records),
        n_local_scored=sum(1 for v in local_results.values() if v),
        n_window_scored=aggregates["n_window_probes_scored"],
        aggregates=aggregates,
    )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{session_id}.judge.json"
    out_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Judge summary written to %s", out_path)

    return report


# CLI -------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run the LLM judge on a finished session.")
    parser.add_argument("session_id", help="Langfuse session id (matches the on-disk log stem).")
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--output-dir", type=Path, default=Path("logs/judge"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        for noisy in ("httpx", "httpcore", "anthropic"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    from dotenv import load_dotenv
    load_dotenv(override=False)

    report = asyncio.run(judge_session(
        args.session_id,
        annotations_path=args.annotations,
        judge_model=args.model,
        concurrency=args.concurrency,
        output_dir=args.output_dir,
    ))
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
