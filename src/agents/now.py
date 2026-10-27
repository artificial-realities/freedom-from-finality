"""Now: the one LLM call on the critical path, once per beat.

Reads the assembled context (notebook, world, the viewer and Then's
understanding of them, summaries, recent clips, where the picture is)
and returns a `BeatPlan`: one action or state of the story as one to a
few clips, each with its picture, its lines and the world changes that
happen in it, plus how it takes the viewer's input. A call that fails
(it timed out, was refused, came back cut off or not as a beat) is made
once more; when that fails too, `NowFailed` says why, and the film stops
rather than show a clip nobody wrote. The world validator checks the
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
from src.story.context import (
    PAST_FOR_NOW,
    SUMMARIES_FOR_NOW,
    coming_back,
    format_memory,
    format_notebook,
    format_policy,
    format_recent,
    format_viewer,
    format_world_now,
    in_play,
    named_now,
    recalled,
)
from src.story.history import History
from src.story.schemas import BeatPlan
from src.story.validator import check
from src.story.viewer import ViewerMemory
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("now.system.md")
USER_PATH: Path = prompt_path("now.user.md")
# A failed call is made once more before the film stops: past about 10 s
# the picture holds anyway, and two timeouts are already a long wait.
ATTEMPTS = 2


class NowFailed(RuntimeError):
    """Now could not write the beat: every attempt failed; the message says why."""


def clip_cap(config: Config, text: str) -> int:
    if (text or "").strip():
        return max(1, min(config.answer_max_clips, config.max_clips_per_beat))
    return config.max_clips_per_beat


async def _call(system_prompt: str, user_prompt: str, llm: LLMBackend, config: Config,
                clip_no: int) -> tuple[BeatPlan | None, str]:
    parsed, why = await call_llm_structured(
        agent="now",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        clip_no=clip_no,
        schema=BeatPlan,
        max_tokens=config.max_tokens_now,
    )
    if parsed is None:
        return None, why
    try:
        return BeatPlan(**parsed), ""
    except ValidationError as exc:
        logger.warning("Now beat validation failed at clip %s: %s", clip_no, exc)
        return None, f"the reply was not a beat ({exc.error_count()} errors)"


async def run(
    *,
    world: WorldModel,
    history: History,
    viewer: ViewerMemory,
    text: str,
    llm: LLMBackend,
    config: Config,
    beat_status: str,
    clip_seconds: float,
) -> tuple[BeatPlan, list[str], int]:
    """Return (beat, notes, calls): `calls` is how many LLM calls it took
    (a failed attempt and the validator's re-ask each add one). Raises
    `NowFailed` when no attempt gave a beat. `text` is the viewer's input
    this beat answers ("" for none) and `clip_seconds` the delivered
    length of the run's clip shape."""
    system_prompt = load_prompt(
        SYSTEM_PATH,
        policy=format_policy(),
        clip_seconds=seconds_label(clip_seconds),
        beat_seconds=seconds_label(round(clip_seconds * config.max_clips_per_beat)),
        typical_seconds=f"{round(2 * clip_seconds)} to {round(3 * clip_seconds)}",
        max_clips=config.max_clips_per_beat,
    )
    named = named_now(world, text, config.recent_clips, viewer)
    recall = recalled(world, named)
    back = coming_back(world, history, in_play(world, named, recall), shown=SUMMARIES_FOR_NOW)
    user_prompt = load_prompt(
        USER_PATH,
        clip=world.clip,
        notebook=format_notebook(world),
        world_now=format_world_now(world, recall),
        viewer=format_viewer(world, viewer, text, answer_clips=config.answer_max_clips, recall=recall),
        summaries=format_memory(world, history, back, recent=SUMMARIES_FOR_NOW, caps=PAST_FOR_NOW),
        recent=format_recent(world, config.recent_clips),
        beat_status=beat_status,
    )

    notes: list[str] = []
    failures: list[str] = []
    beat = None
    calls = 0
    while beat is None and calls < ATTEMPTS:
        calls += 1
        beat, why = await _call(system_prompt, user_prompt, llm, config, world.clip)
        if beat is None:
            failures.append(why)
            notes.append(f"now: call {calls} failed ({why})")
    if beat is None:
        raise NowFailed("Now could not write the next beat: "
                        + (f"{failures[0]}, twice" if len(set(failures)) == 1 else "; then ".join(failures)))

    report = check(beat, world, max_clips=clip_cap(config, text))
    if report.violations:
        calls += 1
        notes.append("now: first proposal rejected: " + "; ".join(report.violations))
        retry_prompt = (
            user_prompt
            + "\n\n## Corrections required\n\n"
            "Your previous beat was rejected by the world validator:\n"
            + "\n".join(f"- {v}" for v in report.violations)
            + "\n\nReturn a corrected beat as a single JSON object."
        )
        second, why = await _call(system_prompt, retry_prompt, llm, config, world.clip)
        if second is None:
            notes.append(f"now: retry failed ({why}); the first beat is used, its invalid ops dropped")
        else:
            report2 = check(second, world, max_clips=clip_cap(config, text))
            if len(report2.violations) <= len(report.violations):
                beat = second
            if report2.violations:
                notes.append(
                    "now: retry still had violations (invalid ops dropped): "
                    + "; ".join(report2.violations),
                )
            else:
                notes.append("now: retry accepted")
    return beat, notes, calls
