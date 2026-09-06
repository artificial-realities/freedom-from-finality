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

    #: True when the backend owns frame-to-frame continuity itself — a
    #: resident server holding a sliding window of the previous chunk,
    #: for example. The runner then skips extracting each clip's last
    #: frame to re-seed the next render (wasted work, and a lossy
    #: decode/re-encode round trip), and may pipeline the render: it
    #: depends only on the prompt, so it can overlap with the next
    #: turn's agent calls.
    #:
    #: False (the default) means the runner threads the seed image
    #: forward itself, which is what stateless hosted APIs need.
    manages_continuity: bool = False

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
        the backend's configured default.
        """
        ...
