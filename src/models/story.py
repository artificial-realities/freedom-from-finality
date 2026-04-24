"""Story blueprint models — loaded once per session from data/story.json.

All fields are static and read-only during a run. See docs/ARCHITECTURE.md
for the per-field audience table.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class Location(BaseModel):
    name: str
    description: str


class Character(BaseModel):
    name: str
    description: str


class Story(BaseModel):
    title: str
    synopsis: str
    visual_style: str
    tone_guidelines: str
    locations: list[Location] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    world_constraints: list[str] = Field(default_factory=list)
    narrative_premise: str
    long_term_narrative: str = ""
    short_term_narrative: str = ""

    @classmethod
    def from_json(cls, path: Path | str) -> "Story":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))
