"""Runner-facing views of a segment.

The Director engine's real output is `src.story.schemas.BeatPlan`; these
three small models are the projection the runner, playback, history and
transcript consume. They are what a future engine must also produce.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Beat(BaseModel):
    """The narrative view of a segment."""

    narration: str
    action: str
    outcome: str
    short_term_narrative: str
    long_term_narrative: str | None = None


class Shot(BaseModel):
    """The render view of a segment: the compiled video prompt and its anchors."""

    i2v_prompt: str
    on_screen: list[str] = Field(default_factory=list)
    camera: str
    motion: str
    end_frame_description: str
    duration_seconds: float = Field(default=6.0, gt=0, le=15)   # the clip shape's delivered seconds


class Commentary(BaseModel):
    """The spoken voice-over line for a segment. Empty means silence.

    Rendered natively by LTX-2.5 (`narration: ltx`) or through TTS, mixed
    into its clip; a TTS line longer than its clip is not rendered.
    """

    voiceover: str
