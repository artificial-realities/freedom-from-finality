"""Abstract image-to-video backend.

Adapters wrap a hosted or local i2v service; the runtime sees a single
`synthesize` call that takes a seed image + a text prompt and returns
the path to the rendered MP4 (or None on failure — i2v always fails
soft, the way TTS does).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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

    @abstractmethod
    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
        duration: int | None = None,
    ) -> str | None:
        """Render a clip from `image_path` + `prompt`. Return MP4 path or None.

        `duration` overrides the backend's default for this call. None = use
        the backend's configured default. A backend that owns continuity
        seeds its first clip from `image_path` and every later clip from
        the previous one; the story layer never asks for a re-seed.
        """
        ...
