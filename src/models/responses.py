"""Runner-facing views of a segment.

The Now engine's real output is `src.story.schemas.BeatPlan`; these
two small models are the projection the runner, playback, history and
transcript consume. They are what a future engine must also produce.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Beat(BaseModel):
    """The narrative view of a segment: what the camera sees (the prose the
    viewer reads), the image it ends on, and the beat it belongs to."""

    narration: str
    outcome: str
    intent: str = ""


class Shot(BaseModel):
    """The render view of a segment: the compiled video prompt and its anchors."""

    i2v_prompt: str
    on_screen: list[str] = Field(default_factory=list)
    camera: str
    motion: str
    end_frame_description: str
    duration_seconds: float = Field(default=6.0, gt=0, le=15)   # the clip shape's delivered seconds
