"""Director: the one LLM call on the per-segment critical path.

Reads the assembled context (plan, world, threads, viewer, summaries,
recent beats, gates) and returns a `Segment`: narration, shot, audio,
world delta, thread ops, intervention disposition, flags. The world
validator checks the proposal; a hard violation earns exactly one
re-ask with the violations spelled out.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import call_llm_structured
from src.llm.base import LLMBackend
from src.models.config import Config
from src.state.story_state import StoryState
from src.story.context import (
    format_cast,
    format_gates,
    format_interventions,
    format_locations,
    format_plan,
    format_recent,
    format_rules,
    format_summaries,
    format_threads,
    format_world_now,
)
from src.story.schemas import Segment
from src.story.validator import check
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("director.system.md")
USER_PATH: Path = prompt_path("director.user.md")


async def _call(system_prompt: str, user_prompt: str, llm: LLMBackend, config: Config, turn: int) -> Segment | None:
    parsed = await call_llm_structured(
        agent="director",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=turn,
        schema=Segment,
        max_tokens=config.max_tokens_director,
    )
    if parsed is None:
        return None
    try:
        return Segment(**parsed)
    except ValidationError as exc:
        logger.warning("Director segment validation failed on segment %s: %s", turn, exc)
        return None


async def run(
    *,
    world: WorldModel,
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    voiceover_open: bool,
    pacing_note: str,
    scene_note: str,
) -> tuple[Segment | None, list[str]]:
    """Return (segment, notes). `segment` is None on a parse failure."""
    system_prompt = load_prompt(
        SYSTEM_PATH,
        title=state.title or world.title,
        narrative_premise=state.narrative_premise or "(none)",
        world_constraints=format_rules(state.world_constraints),
        visual_style=state.visual_style or "(none)",
        cast=format_cast(world),
        locations=format_locations(world),
        tone_guidelines=state.tone_guidelines or "(no register defined; leave voiceover empty)",
        duration_rule=(
            f"every segment is rendered as exactly {config.clip_seconds} seconds of video. Write action that "
            f"fills {config.clip_seconds} seconds, no more, and set this to {config.clip_seconds}."
            if config.clip_seconds > 0
            else "3 to 15. Default 5. Longer only when the action needs it."
        ),
    )
    user_prompt = load_prompt(
        USER_PATH,
        segment=state.turn_number,
        plan=format_plan(world, config),
        world_now=format_world_now(world),
        threads=format_threads(world),
        interventions=format_interventions(world, state.user_input),
        summaries=format_summaries(world),
        recent=format_recent(world, config.recent_beats),
        gates=format_gates(
            voiceover_open=voiceover_open,
            pacing=pacing_note,
            scene=scene_note,
            narration_mode=config.narration,
        ),
    )

    notes: list[str] = []
    segment = await _call(system_prompt, user_prompt, llm, config, state.turn_number)
    if segment is None:
        return None, ["director: structured parse failed"]

    report = check(segment, world)
    if report.violations:
        notes.append("director: first proposal rejected: " + "; ".join(report.violations))
        retry_prompt = (
            user_prompt
            + "\n\n## Corrections required\n\n"
            "Your previous segment was rejected by the world validator:\n"
            + "\n".join(f"- {v}" for v in report.violations)
            + "\n\nReturn a corrected segment as a single JSON object."
        )
        second = await _call(system_prompt, retry_prompt, llm, config, state.turn_number)
        if second is not None:
            report2 = check(second, world)
            if len(report2.violations) <= len(report.violations):
                segment = second
            if report2.violations:
                notes.append(
                    "director: retry still had violations (invalid ops dropped): "
                    + "; ".join(report2.violations),
                )
            else:
                notes.append("director: retry accepted")
    return segment, notes
