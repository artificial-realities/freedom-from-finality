"""Then: the slow-clock LLM call, off the critical path.

Runs once before the story's first clip and then every `then_every`
clips, and does four things in one call: summarises the clips since its
last run, rewrites the notebook (characters, seeds, intentions),
rewrites its understanding of the viewer and notes their new marks on
the story, and may name a checkpoint where the story turned. Its update
is applied deterministically at the first plan that finds it finished.
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
    PAST_FOR_THEN,
    SUMMARIES_FOR_THEN,
    coming_back,
    format_checkpoints,
    format_clips,
    format_memory,
    format_notebook,
    format_policy,
    format_viewer_then,
    format_world_now,
    in_play,
    named_now,
    recalled,
)
from src.story.history import History
from src.story.schemas import ThenUpdate
from src.story.viewer import ViewerMemory
from src.story.world import WorldModel
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("then.system.md")
USER_PATH: Path = prompt_path("then.user.md")

INSTRUCTIONS = {
    "opening": (
        "The story has not started; there is nothing to summarize. Its first "
        "frame shows: {scene}. Write the opening notebook: for each character "
        "who is present, an empty `personality` (it keeps the one World now "
        "gives it) and a `lately` from the first frame and nothing more, since "
        "nothing has happened yet; your initial sense of what is alive; any "
        "seeds you notice in the first frame; and your opening intentions. The "
        "viewer has said nothing yet: no readings and no marks. There is no "
        "past yet either: leave `summary`, `story_so_far` and `since_checkpoint` "
        "empty, and `checkpoint` null."
    ),
    "summary": (
        "Summarise the clips since your last run into `summary`. Notice whether "
        "the story has turned since its last checkpoint; if it has, name the "
        "checkpoint and rewrite the story so far. Rewrite the story since the "
        "last checkpoint. Then rewrite the notebook: how each character has been lately, and "
        "whether the story has changed who any of them is at heart; who the "
        "story is following and who else is pulling at it; what is alive, "
        "which seeds remain or are new, and where the story might go. Then "
        "rewrite your understanding of the viewer from their inputs, and record "
        "any new mark they have put on the story."
    ),
}


async def run(
    *,
    world: WorldModel,
    history: History,
    viewer: ViewerMemory,
    upto: int,
    scene: str,
    llm: LLMBackend,
    config: Config,
    mode: str,
    clip_seconds: float,
) -> ThenUpdate | None:
    """`viewer` is the viewer's memory and `upto` how many of its records
    this run reads (what came after is for the next). `scene` is what Frame
    Zero's first frame shows; `mode` is "opening" for the run before the
    story's first clip, "summary" for every other."""
    system_prompt = load_prompt(SYSTEM_PATH, policy=format_policy())
    scene = " ".join(scene.split()).rstrip(".")
    named = named_now(world, recent=config.recent_clips, viewer=viewer)
    recall = recalled(world, named)
    back = coming_back(world, history, in_play(world, named, recall), shown=SUMMARIES_FOR_THEN)
    user_prompt = load_prompt(
        USER_PATH,
        clip=world.clip,
        clip_seconds=seconds_label(clip_seconds),
        notebook=format_notebook(world),
        summaries=format_memory(world, history, back, recent=SUMMARIES_FOR_THEN, caps=PAST_FOR_THEN),
        clips=format_clips(world, world.last_summary),
        world_now=format_world_now(world, recall),
        viewer=format_viewer_then(world, viewer, upto=upto, recall=recall),
        checkpoints=format_checkpoints(world, history),
        instruction=INSTRUCTIONS.get(mode, INSTRUCTIONS["summary"]).format(scene=scene),
    )
    parsed, _ = await call_llm_structured(
        agent="then",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        clip_no=world.clip,
        schema=ThenUpdate,
        max_tokens=config.max_tokens_then,
    )
    if parsed is None:
        return None
    try:
        return ThenUpdate(**parsed)
    except ValidationError as exc:
        logger.warning("Then update validation failed at clip %s: %s", world.clip, exc)
        return None
