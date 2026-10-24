"""Then: the slow-clock LLM call, off the critical path.

Runs on triggers (bootstrap or every `compaction_interval` segments)
and does four things in one call: compresses the event log into
summaries, rewrites the notebook (characters, seeds, intentions),
rewrites its understanding of the viewer and notes their new marks on
the story, and may recognise an era. Its update is applied
deterministically at the next join.
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
    format_events,
    format_notebook,
    format_policy,
    format_summaries,
    format_viewer_then,
    format_world_now,
    recalled,
)
from src.story.schemas import ThenUpdate
from src.story.viewer import ViewerMemory
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("then.system.md")
USER_PATH: Path = prompt_path("then.user.md")

INSTRUCTIONS = {
    "bootstrap": (
        "The story has not started; there is nothing to summarize. Its first "
        "frame shows: {scene}. Write the opening notebook: for each character "
        "who is present, an empty `personality` (it keeps the one World now "
        "gives it) and a `lately` from the first frame and nothing more, since "
        "nothing has happened yet; your initial sense of what is alive; any "
        "seeds you notice in the first frame; and your opening intentions. The "
        "viewer has said nothing yet: no readings and no marks."
    ),
    "compact": (
        "Compress the events since the last compaction into `scene_summary`. "
        "Update the act summary if the current phase has changed meaningfully. "
        "Then rewrite the notebook: how each character has been lately, and "
        "whether the story has changed who any of them is at heart; who the "
        "story is following and who else is pulling at it; what is alive, "
        "which seeds remain or are new, and where the story might go. Then "
        "rewrite your understanding of the viewer from their lines, and record "
        "any new mark they have put on the story. Notice if an era has ended."
    ),
}


async def run(
    *,
    world: WorldModel,
    viewer: ViewerMemory,
    upto: int,
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    reason: str,
    mode: str,
    clip_seconds: float,
) -> ThenUpdate | None:
    """`viewer` is the viewer's memory and `upto` how many of its records
    this run reads (what came after is for the next)."""
    system_prompt = load_prompt(SYSTEM_PATH, policy=format_policy())
    scene = " ".join(state.story.scene.split()).rstrip(".")
    recall = recalled(world, recent=config.recent_clips, viewer=viewer)
    user_prompt = load_prompt(
        USER_PATH,
        reason=reason,
        segment=world.segment,
        clip_seconds=seconds_label(clip_seconds),
        notebook=format_notebook(world),
        summaries=format_summaries(world, scene_limit=6, recall=recall),
        events=format_events(world, world.last_compacted),
        world_now=format_world_now(world, recall),
        viewer=format_viewer_then(world, viewer, upto=upto, recall=recall),
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
