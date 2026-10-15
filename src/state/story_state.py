"""Runner-facing session state.

The engine owns the story (see `src.story.world.WorldModel`); this
object is the surface the runner, playback and transcript work with:
the blueprint, the current segment's views and history. The blueprint
is set once at initialization and read-only during a run.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.i2v.shape import ClipShape
from src.models.responses import Beat, Shot
from src.models.story import Story


class HistoryEntry(BaseModel):
    turn: int
    user_input: str
    beat: Beat
    shot: Shot


class StoryState(BaseModel):
    story: Story

    # Current turn
    turn_number: int = 0
    user_input: str = ""
    current_beat: Beat | None = None
    current_shot: Shot | None = None
    # How the Director handled `user_input` this segment (none | absorbed |
    # deferred | declined) and why; the player shows this to the viewer.
    current_intervention: str = "none"
    current_intervention_reason: str = ""
    current_beat_intent: str = ""   # the beat this clip belongs to, in a few words
    current_clip: str = ""          # "2/3": the clip's place in its beat
    # The clip's spoken lines ("Name: line"): the player shows them, and the
    # overlap seam brings a clip with speech in at once instead of
    # crossfading its first syllable.
    current_dialogue: list[str] = Field(default_factory=list)
    current_shape: ClipShape | None = None   # the clip's selected shape: what is rendered and held
    current_clip_id: int = 0                # monotonic clip identity (never reused)
    current_parent_clip_id: int = 0         # the clip this one continues from (0 = reference)
    turn_notes: str = ""  # engine-specific markdown appended to the transcript

    # Compact views of the world model for the transcript (the engine
    # writes them; the world model itself is the source of truth).
    world_state: dict = Field(default_factory=dict)
    context_brief: str = ""

    history: list[HistoryEntry] = Field(default_factory=list)
    config_name: str = ""

    @classmethod
    def initialize(cls, story: Story, config_name: str) -> "StoryState":
        return cls(story=story, config_name=config_name)
