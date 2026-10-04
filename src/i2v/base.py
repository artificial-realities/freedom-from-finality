"""Abstract image-to-video backend.

Adapters wrap a hosted or local i2v service; the runtime sees a single
`synthesize` call that takes a seed image + a text prompt and returns
the path to the rendered MP4 (or None on failure — i2v always fails
soft, the way TTS does).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.i2v.shape import DEFAULT_FPS, ClipShape, ShapeRegistry


class I2VBackend(ABC):
    #: True when the backend owns frame-to-frame continuity itself — a
    #: resident server holding a sliding window of the previous chunk,
    #: for example. The runner may then pipeline the render (it depends
    #: only on the prompt, so it can overlap with the next turn's agent
    #: calls).
    manages_continuity: bool = False

    #: What the backend learned about its last render, for the run's timing
    #: log (the LTX server's per-clip headers, for example). Replaced, never
    #: mutated in place, on every `synthesize`; empty when nothing is known.
    last_render: dict = {}

    @property
    def frame_rate(self) -> float:
        """The frame rate clip shapes are built at."""
        return DEFAULT_FPS

    def check_shapes(self, registry: ShapeRegistry) -> dict:
        """Refuse (raise `UnregisteredShape`) when the renderer cannot render
        every registered shape without compiling mid-film; return what was
        checked for the timing log. A backend with nothing to warm accepts."""
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
        """Render a clip from `image_path` + `prompt`. Return MP4 path or None.

        `shape` is the clip's selected shape: the backend renders its
        `render_frames` and derives nothing itself. A backend that owns continuity
        seeds its first clip from `image_path` and every later clip from
        the previous one; the story layer never asks for a re-seed.
        """
        ...
