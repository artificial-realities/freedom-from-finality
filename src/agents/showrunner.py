"""Showrunner: the slow-clock LLM call, off the critical path.

Runs on triggers (bootstrap, scene exit, stagnation, a replan flag, or
every `compaction_interval` segments) and does two things in one call:
compacts the event log into summaries and writes the plan (arc + next
scene card). Its update is applied deterministically at the next join.
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
    format_cast_rules,
    format_events,
    format_interventions_all,
    format_locations,
    format_plan,
    format_rules,
    format_summaries,
    format_threads,
    format_world_now,
)
from src.story.pacing import stagnation_report
from src.story.schemas import ShowrunnerUpdate
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("showrunner.system.md")
USER_PATH: Path = prompt_path("showrunner.user.md")

INSTRUCTIONS = {
    "bootstrap": (
        "The film has not started; there is nothing to summarize. Write the arc "
        "and the opening scene card from the blueprint (`replan` = true, "
        "`scene_summary` and `act_summary` empty, no `entity_notes`)."
    ),
    "replan": (
        "This is a scene boundary or a stalled scene. Compact the events since "
        "the last compaction into `scene_summary`, then write the NEXT scene card "
        "(`replan` = true). Change something structural if the scene stalled; "
        "otherwise let the next scene grow out of this one."
    ),
    "compact": (
        "This is a compaction pass mid-scene. Summarize the events since the last "
        "compaction. Check the stagnation report: if the scene has stalled (the "
        "stagnation timer has reached your replan threshold and the recent events show "
        "no new props, no structural changes, just variations on the same activity), "
        "set `replan` = true and write a next scene card that introduces something "
        "new. Otherwise keep the current scene (`replan` = false): below that "
        "threshold the Director is the one asked to break the rut."
    ),
}


async def run(
    *,
    world: WorldModel,
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    reason: str,
    mode: str,
) -> ShowrunnerUpdate | None:
    system_prompt = load_prompt(
        SYSTEM_PATH,
        title=state.title or world.title,
        narrative_premise=state.narrative_premise or "(none)",
        long_term_narrative=state.long_term_narrative or "(none)",
        short_term_narrative=state.short_term_narrative or "(none)",
        world_constraints=format_rules(state.world_constraints),
        cast=format_cast(world),
        cast_rules=format_cast_rules(world),
        locations=format_locations(world),
        thread_guidance=state.thread_guidance.strip() or "(none given: judge each thread by the story)",
    )
    user_prompt = load_prompt(
        USER_PATH,
        reason=reason,
        segment=world.segment,
        plan=format_plan(world, config),
        summaries=format_summaries(world, scene_limit=6),
        events=format_events(world, world.last_compacted),
        world_now=format_world_now(world),
        threads=format_threads(world, limit=12),
        interventions=format_interventions_all(world),
        pacing=stagnation_report(world, config),
        instruction=INSTRUCTIONS.get(mode, INSTRUCTIONS["replan"]),
    )
    parsed = await call_llm_structured(
        agent="showrunner",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=world.segment,
        schema=ShowrunnerUpdate,
        max_tokens=config.max_tokens_showrunner,
    )
    if parsed is None:
        return None
    try:
        return ShowrunnerUpdate(**parsed)
    except ValidationError as exc:
        logger.warning("Showrunner update validation failed at segment %s: %s", world.segment, exc)
        return None
