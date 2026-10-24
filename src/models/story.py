"""Frame Zero: the initial visual state for an infinite story.

A frame zero holds what the first frame looks like — the story's name,
who is on screen, where it begins and how it looks — and nothing more.
No personalities, desires, premises or permanent truths: everything
creative emerges from Now and Then as the story develops.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field, field_validator, model_validator


class Voice(BaseModel):
    """How a voice sounds, for the renderer."""

    description: str = ""
    language: str = ""
    accent: str = ""
    delivery: str = ""

    @classmethod
    def coerce(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return cls(description=value)
        return value


class Location(BaseModel):
    """A place the story can be. `look` and `look_short` are for the
    renderer: without a look the video prompt never names the place."""

    name: str
    description: str
    look: str = ""
    look_short: str = ""


class Character(BaseModel):
    name: str
    voice: Voice | None = None
    look: str = ""
    look_short: str = ""
    state: str = ""
    present: bool = True
    location: str = ""

    @field_validator("voice", mode="before")
    @classmethod
    def _voice(cls, value):
        return Voice.coerce(value)


class Reference(BaseModel):
    """The image the first clip is rendered from, and what it shows."""

    image: str
    shows: str = ""


class Picture(BaseModel):
    """How the story looks. `cuts`: whether a clip may cut to a new shot,
    with `cutting` as guidance on when."""

    style: str = ""
    anchor: str = ""
    reference: Reference | None = None
    cuts: bool = False
    cutting: str = ""


class FrameZero(BaseModel):
    name: str
    picture: Picture = Field(default_factory=Picture)
    characters: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_character_locations(self):
        place_names = {loc.name.lower() for loc in self.locations}
        for ch in self.characters:
            if ch.location and ch.location.lower() not in place_names:
                raise ValueError(f"character {ch.name!r} has location {ch.location!r}, "
                                 f"not one of the locations: {[loc.name for loc in self.locations]}")
        return self

    @property
    def reference_image(self) -> str:
        return self.picture.reference.image if self.picture.reference else ""

    @classmethod
    def from_json(cls, path: Path | str) -> "FrameZero":
        """Load a frame zero from a JSON file or a directory containing frame_zero.json."""
        path = Path(path)
        if path.is_dir():
            fz_dir = path
            path = path / "frame_zero.json"
        else:
            fz_dir = path.parent
        fz = cls(**json.loads(path.read_text(encoding="utf-8")))
        ref = fz.picture.reference
        if ref is not None and not Path(ref.image).is_absolute():
            ref.image = str(fz_dir / ref.image)
        fz.check_reference()
        return fz

    def check_reference(self) -> None:
        """The image exists and decodes. How it fits the render frame is
        checked against the server's size when a run starts."""
        if not self.reference_image:
            return
        image = Path(self.reference_image)
        if not image.is_file():
            raise ValueError(f"{self.name}: reference image {image} does not exist")
        try:
            with Image.open(image) as img:
                img.verify()
        except Exception as exc:
            raise ValueError(f"{self.name}: reference image {image} does not decode as an image ({exc})") from exc

