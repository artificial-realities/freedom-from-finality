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
    duration_seconds: int = Field(default=5, ge=1, le=15)


class Commentary(BaseModel):
    """The spoken voice-over line for a segment. Empty means silence.

    When rendered through TTS the line's runtime is measured after
    synthesis; the live producer concatenates as many silent clips behind
    it as the audio length warrants and keeps the voice-over gate closed
    on those turns.
    """

    voiceover: str
