"""Story blueprint models — loaded once per session from a story folder
(`stories/<story>/story.json`, with the story's reference image beside it).

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
    look: str = ""              # visual descriptor for the renderer (falls back to description)
    look_short: str = ""        # a few words of look for clips that continue the picture ("" → name only)
    speaks: bool = True         # False → dialogue from this character is rejected
    reference_pose: str = ""    # what the story's seed image shows (pose, framing); the first clip starts from it


class Story(BaseModel):
    title: str
    synopsis: str
    visual_style: str
    visual_anchor: str = ""     # one sentence of look and framing for clips that continue the picture ("" → the first sentence of visual_style)
    tone_guidelines: str
    locations: list[Location] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    world_constraints: list[str] = Field(default_factory=list)
    narrative_premise: str
    long_term_narrative: str = ""
    short_term_narrative: str = ""
    seed_image: str = ""        # reference image for the video model, resolved relative to the story dir
    cast_locked: bool = False   # True → no new characters may be introduced (props are fine)

    @classmethod
    def from_json(cls, path: Path | str) -> "Story":
        """Load a story from a JSON file or a directory containing story.json."""
        path = Path(path)
        if path.is_dir():
            story_dir = path
            path = path / "story.json"
        else:
            story_dir = path.parent
        story = cls(**json.loads(path.read_text(encoding="utf-8")))
        if story.seed_image and not Path(story.seed_image).is_absolute():
            story.seed_image = str(story_dir / story.seed_image)
        return story
