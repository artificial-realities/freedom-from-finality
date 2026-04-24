"""Shared state for an interactive storytelling session.

Lean and text-first. Structured portions are small and mergeable.
Blueprint fields are set once at initialization; rolling memory is
maintained by Spock; narrative direction is maintained by Tolkien.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.responses import Beat, Commentary, Shot, WorldStateDelta
from src.models.story import Character, Location, Story


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

    # Persistent memory — maintained by Spock
    world_state: dict = Field(default_factory=dict)
    narrative_memory: str = ""
    context_brief: str = ""

    # Narrative direction — maintained by Tolkien
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
    tone_guidelines: str = ""
    locations: list[Location] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    world_constraints: list[str] = Field(default_factory=list)
    narrative_premise: str = ""

    def get_recent_history(self, count: int = 5) -> list[HistoryEntry]:
        if count <= 0:
            return []
        return self.history[-count:]

    def apply_world_delta(self, delta: WorldStateDelta) -> None:
        """Merge a `WorldStateDelta` into `world_state` in place.

        Partial semantics per docs/ARCHITECTURE.md:
        - `protagonist_location`: empty = unchanged.
        - `inventory`: None = unchanged; list = replacement.
        - `characters`: per-name partial merge.
        """
        if delta.protagonist_location:
            self.world_state["protagonist_location"] = delta.protagonist_location

        if delta.inventory is not None:
            self.world_state["inventory"] = list(delta.inventory)

        if delta.characters:
            existing = self.world_state.setdefault("characters", {})
            for name, updates in delta.characters.items():
                if not isinstance(updates, dict):
                    continue
                existing.setdefault(name, {}).update(updates)

    @classmethod
    def initialize(cls, story: Story, config_name: str) -> "StoryState":
        return cls(
            title=story.title,
            synopsis=story.synopsis,
            visual_style=story.visual_style,
            tone_guidelines=story.tone_guidelines,
            locations=list(story.locations),
            characters=list(story.characters),
            world_constraints=list(story.world_constraints),
            narrative_premise=story.narrative_premise,
            long_term_narrative=story.long_term_narrative,
            short_term_narrative=story.short_term_narrative,
            config_name=config_name,
        )
