"""Story blueprint models — loaded once per session from a story folder
(`stories/<story>/story.json`, with the story's reference image beside it).

A blueprint holds the essential story, not a script: what the film is
(`premise`, `question`, `engine`, `opening`, `truths`), who tells it
(`storyteller`), who is in it (`cast`, `locations`) and how it looks
(`picture`). Everything that happens in time is composed live by the
Showrunner, the Director and the viewer. All fields are read-only
during a run. What each field is and what it does to the film, for an
author: stories/README.md.
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


def perspective_key(text: str) -> str:
    """A perspective as the policy compares it: "First person" → "first-person"."""
    return re.sub(r"[^a-z]+", "-", text.strip().lower()).strip("-")


class Storyteller(BaseModel):
    """Who tells the story: what makes the film the author's even when the
    viewer steers. Every field is optional; empty ones are not said."""

    theme: str = ""             # what the story is about: the value at stake
    tone: str = ""              # the register of the whole film
    temperament: str = ""       # how viewer impulses become this story's currency, and its rhythm
    never: list[str] = Field(default_factory=list)   # what this storyteller would never do


class Location(BaseModel):
    name: str
    description: str


class Character(BaseModel):
    name: str
    who: str = ""               # who they are in the story
    wants: str = ""             # what they want; what they do when the viewer gives them something
    nature: str = ""            # how they are: temperament, habits, the flaw
    voice: Voice | None = None  # how they sound when they speak
    look: str = ""              # verbatim for the renderer: the full look, on entering the picture
    look_short: str = ""        # the look on clips that continue the picture ("" → name only)
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


class Cast(BaseModel):
    """Who is in the film: the characters, the one the film follows, and
    who else may join. `newcomers` says what kind of characters the film
    may introduce beyond the cast; empty means none (anything else that
    appears is a prop)."""

    protagonist: str = ""       # a character's name; "" = the first character
    characters: list[Character] = Field(default_factory=list)
    newcomers: str = ""


class Perspective(BaseModel):
    """How the film is shot: its default perspective ("third-person",
    "first-person", "observer", …), the perspectives a beat may switch to,
    and when a switch is right. The Director declares a switch in
    `BeatPlan.perspective`; the validator refuses one the story does not
    allow. `cuts` (LTX-15): whether a clip may cut to a new shot
    (`ClipSpec.cut_to`), with `cutting` as the story's guidance on when;
    without it the film is one continuous take and a cut is dropped."""

    default: str = "third-person"
    allowed: list[str] = Field(default_factory=list)
    switching: str = ""
    cuts: bool = False
    cutting: str = ""

    def permits(self, perspective: str) -> bool:
        key = perspective_key(perspective)
        return not key or key in {perspective_key(p) for p in [self.default, *self.allowed]}


class Reference(BaseModel):
    """The image the first clip is rendered from, and what it shows in
    words (the Director cannot see it). `shows` names any character in
    the image as the cast names it; a character it does not name enters
    the picture during the first clip."""

    image: str                  # resolved relative to the story folder
    shows: str = ""


class Picture(BaseModel):
    """How the film looks, for the Director and the compiler."""

    style: str = ""             # the full style, on the film's first clip
    anchor: str = ""            # one sentence of look and framing for later clips ("" → the style's first sentence)
    reference: Reference | None = None
    perspective: Perspective = Field(default_factory=Perspective)


class Story(BaseModel):
    title: str
    premise: str                # the situation, one or two sentences
    question: str = ""          # the dramatic question the film keeps open
    engine: str = ""            # what keeps producing scenes: the repeatable pattern
    opening: str = ""           # where the film starts: the routine before anything tilts it
    truths: list[str] = Field(default_factory=list)   # what holds whatever the viewer does
    storyteller: Storyteller = Field(default_factory=Storyteller)
    locations: list[Location] = Field(default_factory=list)
    cast: Cast = Field(default_factory=Cast)
    picture: Picture = Field(default_factory=Picture)

    @model_validator(mode="after")
    def _known_protagonist(self) -> "Story":
        names = [c.name for c in self.cast.characters]
        if self.cast.protagonist and self.cast.protagonist not in names:
            raise ValueError(f"protagonist {self.cast.protagonist!r} is not one of the characters")
        return self

    def protagonist_character(self) -> Character | None:
        """The protagonist: the character named `cast.protagonist`, or the first."""
        chars = self.cast.characters
        return next((c for c in chars if c.name == self.cast.protagonist), None) or (chars[0] if chars else None)

    @property
    def reference_image(self) -> str:
        """The reference image's path, or "" for a story without one."""
        return self.picture.reference.image if self.picture.reference else ""

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
        ref = story.picture.reference
        if ref is not None and not Path(ref.image).is_absolute():
            ref.image = str(story_dir / ref.image)
        story.check_reference()
        return story

    def check_reference(self) -> None:
        """The static half of the reference check (RUN-11): the image exists
        and decodes. How it fits the render frame is checked against the
        server's size when a run starts (`LtxvI2V.check_reference`).
        Raises ValueError."""
        if not self.reference_image:
            return
        image = Path(self.reference_image)
        if not image.is_file():
            raise ValueError(f"{self.title}: reference image {image} does not exist")
        try:
            with Image.open(image) as img:
                img.verify()
        except Exception as exc:
            raise ValueError(f"{self.title}: reference image {image} does not decode as an image ({exc})") from exc
