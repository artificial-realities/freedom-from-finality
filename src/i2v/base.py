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
    model: str
    resolution: str
    duration: int

    @abstractmethod
    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
    ) -> str | None:
        """Render a clip from `image_path` + `prompt`. Return MP4 path or None."""
        ...
