"""Now: the one LLM call on the critical path, once per beat.

Reads the assembled context (notebook, world, the viewer and Then's
understanding of them, summaries, recent clips, where the picture is) and returns a `BeatPlan`:
one action or state of the story as one to a few clips, each with its
picture, its lines and the world changes that happen in it, plus the
viewer disposition. The world validator checks the proposal; a hard
violation earns exactly one re-ask with the violations spelled out.
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
    format_notebook,
    format_policy,
    format_recent,
    format_summaries,
    format_viewer,
    format_world_now,
    recalled,
)
from src.story.schemas import BeatPlan
from src.story.validator import check
from src.story.viewer import ViewerMemory
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("now.system.md")
USER_PATH: Path = prompt_path("now.user.md")


def clip_cap(config: Config, user_input: str) -> int:
    if (user_input or "").strip():
        return max(1, min(config.answer_max_clips, config.max_clips_per_beat))
    return config.max_clips_per_beat


async def _call(system_prompt: str, user_prompt: str, llm: LLMBackend, config: Config, turn: int) -> BeatPlan | None:
    parsed = await call_llm_structured(
        agent="now",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=turn,
        schema=BeatPlan,
        max_tokens=config.max_tokens_now,
    )
    if parsed is None:
        return None
    try:
        return BeatPlan(**parsed)
    except ValidationError as exc:
        logger.warning("Now beat validation failed at segment %s: %s", turn, exc)
        return None


async def run(
    *,
    world: WorldModel,
    viewer: ViewerMemory,
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    beat_status: str,
    clip_seconds: float,
) -> tuple[BeatPlan | None, list[str], bool]:
    """Return (beat, notes, retried). `beat` is None on a parse failure;
    `retried` says the validator re-ask was made (a second LLM call).
    `clip_seconds` is the delivered length of the run's clip shape."""
    system_prompt = load_prompt(
        SYSTEM_PATH,
        policy=format_policy(),
        clip_seconds=seconds_label(clip_seconds),
        beat_seconds=seconds_label(round(clip_seconds * config.max_clips_per_beat)),
        typical_seconds=f"{round(2 * clip_seconds)} to {round(3 * clip_seconds)}",
        max_clips=config.max_clips_per_beat,
    )
    recall = recalled(world, state.user_input, config.recent_clips, viewer)
    user_prompt = load_prompt(
        USER_PATH,
        segment=state.turn_number,
        notebook=format_notebook(world),
        world_now=format_world_now(world, recall),
        viewer=format_viewer(world, viewer, state.user_input, answer_clips=config.answer_max_clips, recall=recall),
        summaries=format_summaries(world, recall=recall),
        recent=format_recent(world, config.recent_clips),
        beat_status=beat_status,
    )

    notes: list[str] = []
    beat = await _call(system_prompt, user_prompt, llm, config, state.turn_number)
    if beat is None:
        return None, ["now: structured parse failed"], False

    report = check(beat, world, max_clips=clip_cap(config, state.user_input))
    retried = bool(report.violations)
    if report.violations:
        notes.append("now: first proposal rejected: " + "; ".join(report.violations))
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
                    "now: retry still had violations (invalid ops dropped): "
                    + "; ".join(report2.violations),
                )
            else:
                notes.append("now: retry accepted")
    return beat, notes, retried
