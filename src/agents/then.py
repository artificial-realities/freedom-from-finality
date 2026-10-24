"""Then: the slow-clock LLM call, off the critical path.

Runs on triggers (bootstrap or every `compaction_interval` segments)
and does three things in one call: compresses the event log into
summaries, rewrites the notebook (characters, seeds, intentions,
audience), and may recognise an era. Its update is applied
deterministically at the next join.
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
    format_events,
    format_interventions_all,
    format_notebook,
    format_policy,
    format_summaries,
    format_world_now,
)
from src.story.schemas import ThenUpdate
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("then.system.md")
USER_PATH: Path = prompt_path("then.user.md")

INSTRUCTIONS = {
    "bootstrap": (
        "The story has not started; there is nothing to summarize. Its first "
        "frame shows: {scene}. Write the opening notebook: a portrait of each "
        "character who is present, from who World now says it is and nothing "
        "more, since nothing has happened yet; your initial sense of what is "
        "alive; any seeds you notice in the first frame; and your opening "
        "intentions. The audience section is empty."
    ),
    "compact": (
        "Compress the events since the last compaction into `scene_summary`. "
        "Update the act summary if the current phase has changed meaningfully. "
        "Then rewrite the notebook: who has each character become, what is "
        "alive, which seeds remain or are new, where the story might go, and "
        "what you sense about the audience. Notice if an era has ended."
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
) -> ThenUpdate | None:
    system_prompt = load_prompt(SYSTEM_PATH, policy=format_policy())
    scene = " ".join(state.story.scene.split()).rstrip(".")
    user_prompt = load_prompt(
        USER_PATH,
        reason=reason,
        segment=world.segment,
        notebook=format_notebook(world),
        summaries=format_summaries(world, scene_limit=6),
        events=format_events(world, world.last_compacted),
        world_now=format_world_now(world),
        interventions=format_interventions_all(world),
        instruction=INSTRUCTIONS.get(mode, INSTRUCTIONS["compact"]).format(scene=scene),
    )
    parsed = await call_llm_structured(
        agent="then",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=world.segment,
        schema=ThenUpdate,
        max_tokens=config.max_tokens_then,
    )
    if parsed is None:
        return None
    try:
        return ThenUpdate(**parsed)
    except ValidationError as exc:
        logger.warning("Then update validation failed at segment %s: %s", world.segment, exc)
        return None
