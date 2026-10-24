"""Frame zero: the opening scene every adventure starts from.

A frame zero is the first frame and nothing more: a sentence saying what
it shows, the one character in it (its name, how it looks, who it is to
begin with and where it stands), the place it stands in, and the image
the first clip is rendered from. None of it is a rule: once the story
starts it is ordinary world state, and Now and Then may change any of
it. What holds in every adventure is the content policy
(`src/prompts/policy.md`), not the frame zero.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict


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


class StartingCharacter(BaseModel):
    """The character in the first frame. `personality` is who it is when
    the story begins, until Then's notebook has a portrait of it; `state`
    is where it stands and what it is doing."""

    model_config = ConfigDict(extra="forbid")

    name: str
    look: str
    personality: str = ""
    state: str = ""


class StartingLocation(BaseModel):
    """The place the story begins in, drawn from its `look`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    look: str


class FrameZero(BaseModel):
    """`scene` is what the first frame shows. `reference_image`, a path
    relative to the file, is that frame for the renderer: only a video
    run needs it. Unknown keys are refused, so a stale field or a typo is
    an error instead of being silently ignored."""

    model_config = ConfigDict(extra="forbid")

    scene: str
    character: StartingCharacter
    location: StartingLocation
    reference_image: str = ""

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
        if fz.reference_image and not Path(fz.reference_image).is_absolute():
            fz.reference_image = str(fz_dir / fz.reference_image)
        fz.check_reference()
        return fz

    def check_reference(self) -> None:
        """The image exists and decodes. How it fits the render frame is
        checked against the server's size when a run starts."""
        if not self.reference_image:
            return
        image = Path(self.reference_image)
        if not image.is_file():
            raise ValueError(f"reference image {image} does not exist")
        try:
            with Image.open(image) as img:
                img.verify()
        except Exception as exc:
            raise ValueError(f"reference image {image} does not decode as an image ({exc})") from exc
