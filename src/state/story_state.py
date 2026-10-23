"""Runner-facing session state.

The engine owns the story (see `src.story.world.WorldModel`); this
object is the surface the runner, playback and transcript work with:
the frame zero, the current segment's views and history. The frame zero
is set once at initialization and read-only during a run.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.i2v.shape import ClipShape
from src.models.responses import Beat, Shot
from src.models.story import FrameZero


class HistoryEntry(BaseModel):
    turn: int
    user_input: str
    beat: Beat
    shot: Shot


class StoryState(BaseModel):
    story: FrameZero

    turn_number: int = 0
    user_input: str = ""
    current_beat: Beat | None = None
    current_shot: Shot | None = None
    current_intervention: str = "none"
    current_intervention_reason: str = ""
    current_beat_intent: str = ""
    current_clip: str = ""
    current_dialogue: list[str] = Field(default_factory=list)
    current_shape: ClipShape | None = None
    current_clip_id: int = 0
    current_parent_clip_id: int = 0
    turn_notes: str = ""

    world_state: dict = Field(default_factory=dict)
    context_brief: str = ""

    history: list[HistoryEntry] = Field(default_factory=list)
    config_name: str = ""

    @classmethod
    def initialize(cls, story: FrameZero, config_name: str) -> "StoryState":
        return cls(story=story, config_name=config_name)
