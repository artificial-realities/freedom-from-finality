"""Director: the one LLM call on the critical path, once per beat.

Reads the assembled context (plan, world, threads, viewer, summaries,
recent clips, where the picture is, gates) and returns a `BeatPlan`:
one action or state of the film as one to a few clips, each with its
picture and the world changes that happen in it, plus voice-over, thread
ops, the viewer disposition and flags. The world validator checks the
proposal; a hard violation earns exactly one re-ask with the violations
spelled out.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import call_llm_structured
from src.i2v.shape import seconds_label
from src.llm.base import LLMBackend
from src.models.config import Config
from src.state.story_state import StoryState
from src.story.context import (
    format_cast,
    format_cast_rules,
    format_gates,
    format_interventions,
    format_locations,
    format_narrator,
    format_plan,
    format_recent,
    format_rules,
    format_summaries,
    format_threads,
    format_world_now,
)
from src.story.schemas import BeatPlan
from src.story.validator import check
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("director.system.md")
USER_PATH: Path = prompt_path("director.user.md")


def clip_cap(config: Config, user_input: str) -> int:
    """How many clips the beat may have: fewer when it answers a line."""
    if (user_input or "").strip():
        return max(1, min(config.answer_max_clips, config.max_clips_per_beat))
    return config.max_clips_per_beat


async def _call(system_prompt: str, user_prompt: str, llm: LLMBackend, config: Config, turn: int) -> BeatPlan | None:
    parsed = await call_llm_structured(
        agent="director",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=turn,
        schema=BeatPlan,
        max_tokens=config.max_tokens_director,
    )
    if parsed is None:
        return None
    try:
        return BeatPlan(**parsed)
    except ValidationError as exc:
        logger.warning("Director beat validation failed at segment %s: %s", turn, exc)
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
    beat_status: str,
    clip_seconds: float,
) -> tuple[BeatPlan | None, list[str], bool]:
    """Return (beat, notes, retried). `beat` is None on a parse failure;
    `retried` says the validator re-ask was made (a second LLM call).
    `clip_seconds` is the delivered length of the run's clip shape."""
    system_prompt = load_prompt(
        SYSTEM_PATH,
        title=state.title or world.title,
        narrative_premise=state.narrative_premise or "(none)",
        world_constraints=format_rules(state.world_constraints),
        visual_style=state.visual_style or "(none)",
        cast=format_cast(world),
        cast_rules=format_cast_rules(world),
        locations=format_locations(world),
        narrator=format_narrator(state.narrator, state.tone_guidelines),
        clip_seconds=seconds_label(clip_seconds),
        # Beat lengths in whole seconds from the shape and the clip cap (ARCH-10).
        beat_seconds=seconds_label(round(clip_seconds * config.max_clips_per_beat)),
        typical_seconds=f"{round(2 * clip_seconds)} to {round(3 * clip_seconds)}",
        max_clips=config.max_clips_per_beat,
    )
    user_prompt = load_prompt(
        USER_PATH,
        segment=state.turn_number,
        plan=format_plan(world, config),
        world_now=format_world_now(world),
        threads=format_threads(world),
        interventions=format_interventions(world, state.user_input),
        summaries=format_summaries(world),
        recent=format_recent(world, config.recent_clips),
        beat_status=beat_status,
        gates=format_gates(
            voiceover_open=voiceover_open,
            pacing=pacing_note,
            scene=scene_note,
            narrator=state.narrator.enabled,
            answer_clips=config.answer_max_clips if (state.user_input or "").strip() else 0,
        ),
    )

    notes: list[str] = []
    beat = await _call(system_prompt, user_prompt, llm, config, state.turn_number)
    if beat is None:
        return None, ["director: structured parse failed"], False

    report = check(beat, world, max_clips=clip_cap(config, state.user_input))
    retried = bool(report.violations)
    if report.violations:
        notes.append("director: first proposal rejected: " + "; ".join(report.violations))
        retry_prompt = (
            user_prompt
            + "\n\n## Corrections required\n\n"
            "Your previous beat was rejected by the world validator:\n"
            + "\n".join(f"- {v}" for v in report.violations)
            + "\n\nReturn a corrected beat as a single JSON object."
        )
        second = await _call(system_prompt, retry_prompt, llm, config, state.turn_number)
        if second is not None:
            report2 = check(second, world, max_clips=clip_cap(config, state.user_input))
            if len(report2.violations) <= len(report.violations):
                beat = second
            if report2.violations:
                notes.append(
                    "director: retry still had violations (invalid ops dropped): "
                    + "; ".join(report2.violations),
                )
            else:
                notes.append("director: retry accepted")
    return beat, notes, retried
