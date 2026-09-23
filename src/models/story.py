"""Story blueprint models — loaded once per session from a story folder
(`stories/<story>/story.json`, with the story's reference image beside it).

All fields are static and read-only during a run. See docs/ARCHITECTURE.md
for the per-field audience table.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
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
    thread_guidance: str = ""   # optional: how long setups, promises and gags should run in this story (the Showrunner's)
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
        story.check_reference()
        return story

    def check_reference(self) -> None:
        """The static half of the reference check (RUN-11): the seed image
        exists and decodes, and the protagonist's `reference_pose` says what
        it shows, since the first clip starts from exactly that pose. How it
        fits the render frame is checked against the server's size when a
        run starts (`LtxvI2V.check_reference`). Raises ValueError."""
        if not self.seed_image:
            return
        image = Path(self.seed_image)
        if not image.is_file():
            raise ValueError(f"{self.title}: seed_image {image} does not exist")
        try:
            with Image.open(image) as img:
                img.verify()
        except Exception as exc:
            raise ValueError(f"{self.title}: seed_image {image} does not decode as an image ({exc})") from exc
        if not self.characters or not self.characters[0].reference_pose.strip():
            raise ValueError(
                f"{self.title}: the protagonist (the first character) needs a `reference_pose` "
                "describing what the seed image shows; the first clip starts from it"
            )
