"""The renderer boundary.

The live loop sees one `synthesize` call that takes the reference image
and a compiled prompt and returns the path to the rendered MP4, or None on
failure (rendering always fails soft). The renderer owns continuity: it
seeds the first clip from the reference and every later clip from the
previous one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.i2v.shape import DEFAULT_FPS, ClipShape


class I2VBackend(ABC):
    #: What the backend learned about its last render, for the run's timing
    #: log (the LTX server's per-clip headers, for example). Replaced, never
    #: mutated in place, on every `synthesize`; empty when nothing is known.
    last_render: dict = {}

    @property
    def frame_rate(self) -> float:
        """The frame rate clip shapes are built at."""
        return DEFAULT_FPS

    def check_shape(self, shape: ClipShape) -> dict:
        """Refuse (raise `RendererNotReady`) when the renderer cannot render
        `shape` without compiling mid-film; return what was checked for the
        timing log. A backend with nothing to warm accepts."""
        return {}

    def check_reference(self, path: Path | str) -> dict:
        """Validate the reference image before the run renders anything;
        return what was found for the timing log ({} when the backend has
        nothing to check). Raise to refuse the run."""
        return {}

    @abstractmethod
    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
        shape: ClipShape,
        clip_id: int | None = None,
        parent_clip_id: int | None = None,
    ) -> str | None:
        """Render a clip of `shape.render_frames` from `image_path` + `prompt`.
        Return the MP4 path or None."""
        ...
