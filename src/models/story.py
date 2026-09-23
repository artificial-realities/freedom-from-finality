"""Story blueprint models — loaded once per session from a story folder
(`stories/<story>/story.json`, with the story's reference image beside it).

All fields are static and read-only during a run. See docs/ARCHITECTURE.md
for the per-field audience table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field, field_validator, model_validator


class Voice(BaseModel):
    """How a voice sounds, for the renderer: its character, the language
    it speaks, an accent, and a default delivery. Empty fields are not
    said. A blueprint may give a plain string, read as `description`."""

    description: str = ""       # vocal characteristics: "deep, gravelly, slow", "bright child's voice"
    language: str = ""          # the language it speaks; "" = not stated in the prompt
    accent: str = ""            # "a soft Scottish accent"
    delivery: str = ""          # a default delivery for lines that give none: "quiet, deliberate"

    @classmethod
    def coerce(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return cls(description=value)
        return value


class Narrator(BaseModel):
    """The film's narrator (AUD-06): whether there is one, the register of
    its lines (the Director writes them), how it sounds (the renderer says
    it), and the story's own guidance on when it speaks. There is no
    universal frequency; `min_pause_seconds` is an optional floor of
    silence between two lines for a story that wants one (0 = none)."""

    enabled: bool = True
    tone: str = ""              # the register of the lines, for the Director
    voice: Voice = Field(default_factory=Voice)
    guidance: str = ""          # when narration serves this film (and when dialogue or silence does)
    min_pause_seconds: float = 0.0

    @field_validator("voice", mode="before")
    @classmethod
    def _voice(cls, value):
        return Voice.coerce(value) or Voice()


def perspective_key(text: str) -> str:
    """A perspective as the policy compares it: "First person" → "first-person"."""
    return re.sub(r"[^a-z]+", "-", text.strip().lower()).strip("-")


class CastPolicy(BaseModel):
    """Who may appear besides the blueprint's cast (ARCH-19). A story that
    allows no new characters keeps its cast as written (objects and
    creatures can still be props); one that allows them says what they
    should be like, or leaves it to the film ("" = decide within the
    rules of the world)."""

    dynamic_characters: bool = False
    dynamic_guidance: str = ""


class Perspective(BaseModel):
    """How the film is shot (ARCH-19): its default perspective
    ("third-person", "first-person", "observer", …), the perspectives a
    beat may switch to, and when a switch is right. The Director declares
    a switch in `BeatPlan.perspective`; the validator refuses one the story
    does not allow."""

    default: str = "third-person"
    allowed: list[str] = Field(default_factory=list)
    switching: str = ""

    def permits(self, perspective: str) -> bool:
        key = perspective_key(perspective)
        return not key or key in {perspective_key(p) for p in [self.default, *self.allowed]}


class Location(BaseModel):
    name: str
    description: str


class Character(BaseModel):
    name: str
    description: str
    look: str = ""              # visual descriptor for the renderer (falls back to description)
    look_short: str = ""        # a few words of look for clips that continue the picture ("" → name only)
    speaks: bool = True         # False → dialogue from this character is rejected
    voice: Voice | None = None  # how the character sounds when it speaks (LTX-05)
    reference_pose: str = ""    # what the story's seed image shows (pose, framing); the first clip starts from it
    # ARCH-19: who the character is in the story and what must never drift.
    role: str = ""              # "protagonist", "the keeper's nephew", …
    personality: str = ""
    invariants: list[str] = Field(default_factory=list)   # characteristics that must not change over the film
    # The starting state (the protagonist's is where the first clip starts).
    posture: str = ""
    activity: str = ""
    position: str = ""
    # Supporting characters: whether and when they enter the film. The
    # Showrunner decides when an entrance serves the story; the Director
    # stages it. Ignored for the protagonist, who is in the film from the start.
    appears: Literal["must", "should", "may"] = "may"
    appears_when: str = ""      # the narrative condition, "" = whenever it serves the story

    @field_validator("voice", mode="before")
    @classmethod
    def _voice(cls, value):
        return Voice.coerce(value)


class Story(BaseModel):
    title: str
    synopsis: str
    visual_style: str
    visual_anchor: str = ""     # one sentence of look and framing for clips that continue the picture ("" → the first sentence of visual_style)
    tone_guidelines: str = ""   # the film's tone; a story without `narrator` reads it as the narrator's register
    narrator: Narrator | None = None   # absent → built from `tone_guidelines` (a blueprint from before AUD-06)
    locations: list[Location] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    world_constraints: list[str] = Field(default_factory=list)
    narrative_premise: str
    long_term_narrative: str = ""
    short_term_narrative: str = ""
    thread_guidance: str = ""   # optional: how long setups, promises and gags should run in this story (the Showrunner's)
    seed_image: str = ""        # reference image for the video model, resolved relative to the story dir
    protagonist: str = ""       # the protagonist's name; "" = the first character
    cast_policy: CastPolicy | None = None   # absent → from `cast_locked` (a blueprint from before ARCH-19)
    perspective: Perspective = Field(default_factory=Perspective)
    cast_locked: bool = False   # before ARCH-19: True = no new characters; read into `cast_policy`

    @model_validator(mode="after")
    def _defaults(self) -> "Story":
        if self.narrator is None:
            # A blueprint from before AUD-06: its tone guidelines were the
            # voice-over register, and a story without them had no narrator.
            self.narrator = Narrator(enabled=bool(self.tone_guidelines.strip()), tone=self.tone_guidelines)
        if self.cast_policy is None:
            self.cast_policy = CastPolicy(dynamic_characters=not self.cast_locked)
        if self.protagonist and not any(c.name == self.protagonist for c in self.characters):
            raise ValueError(f"protagonist {self.protagonist!r} is not one of the characters")
        return self

    def protagonist_character(self) -> Character | None:
        """The protagonist: the character named `protagonist`, or the first."""
        return next((c for c in self.characters if c.name == self.protagonist), None) or (
            self.characters[0] if self.characters else None)

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
        hero = self.protagonist_character()
        if hero is None or not hero.reference_pose.strip():
            raise ValueError(
                f"{self.title}: the protagonist needs a `reference_pose` "
                "describing what the seed image shows; the first clip starts from it"
            )
