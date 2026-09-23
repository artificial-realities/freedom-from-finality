"""Runner-facing session state.

The engine owns the story (see `src.story.world.WorldModel`); this
object is the surface the runner, playback and transcript work with:
the blueprint, the current segment's views, pacing bookkeeping and
history. Blueprint fields are set once at initialization.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.responses import Beat, Commentary, Shot
from src.models.story import CastPolicy, Character, Location, Narrator, Perspective, Story


class HistoryEntry(BaseModel):
    turn: int
    user_input: str
    beat: Beat
    shot: Shot
    commentary: Commentary


class StoryState(BaseModel):
    # Current turn
    turn_number: int = 0
    user_input: str = ""
    current_beat: Beat | None = None
    current_shot: Shot | None = None
    current_commentary: Commentary | None = None
    current_audio_path: str = ""  # Set when TTS produced audio for the voice-over
    # How the Director handled `user_input` this segment (none | absorbed |
    # deferred | declined) and why; the player shows this to the viewer.
    current_intervention: str = "none"
    current_intervention_reason: str = ""
    current_beat_intent: str = ""   # the beat this clip belongs to, in a few words
    current_clip: str = ""          # "2/3": the clip's place in its beat
    turn_notes: str = ""  # engine-specific markdown appended to the transcript

    # Compact views of the world model for the transcript (the engine
    # writes them; the world model itself is the source of truth).
    world_state: dict = Field(default_factory=dict)
    context_brief: str = ""

    # Narrator-clock pacing (gating decisions live in the turn engine).
    # `silence_seconds`: story seconds since the last voice-over line, held
    # against the story narrator's optional floor. It starts at the floor,
    # so the film may open on a line.
    # `last_clip_duration`: measured length of the most recently rendered clip.
    # `pacing_managed`: True when the live producer measures these fields
    # from real playback; otherwise the engine counts story-time itself
    # (one turn ≈ one clip of the configured duration).
    silence_seconds: float = 0.0
    last_clip_duration: float = 0.0
    pacing_managed: bool = False

    # Narrative direction (views of the segment's next_direction)
    long_term_narrative: str = ""
    short_term_narrative: str = ""

    # History
    history: list[HistoryEntry] = Field(default_factory=list)

    # Meta
    config_name: str = ""

    # Blueprint — set once at initialization, read-only during a run
    title: str = ""
    synopsis: str = ""
    visual_style: str = ""
    visual_anchor: str = ""
    tone_guidelines: str = ""
    narrator: Narrator = Field(default_factory=Narrator)
    locations: list[Location] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    world_constraints: list[str] = Field(default_factory=list)
    narrative_premise: str = ""
    thread_guidance: str = ""
    protagonist: str = ""
    cast_policy: CastPolicy = Field(default_factory=CastPolicy)
    perspective: Perspective = Field(default_factory=Perspective)

    def get_recent_history(self, count: int = 5) -> list[HistoryEntry]:
        if count <= 0:
            return []
        return self.history[-count:]

    @classmethod
    def initialize(cls, story: Story, config_name: str) -> "StoryState":
        return cls(
            title=story.title,
            synopsis=story.synopsis,
            visual_style=story.visual_style,
            visual_anchor=story.visual_anchor,
            tone_guidelines=story.tone_guidelines,
            narrator=story.narrator,
            silence_seconds=story.narrator.min_pause_seconds,
            locations=list(story.locations),
            characters=list(story.characters),
            world_constraints=list(story.world_constraints),
            narrative_premise=story.narrative_premise,
            thread_guidance=story.thread_guidance,
            protagonist=story.protagonist,
            cast_policy=story.cast_policy,
            perspective=story.perspective,
            long_term_narrative=story.long_term_narrative,
            short_term_narrative=story.short_term_narrative,
            config_name=config_name,
        )
