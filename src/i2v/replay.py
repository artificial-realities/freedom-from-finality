"""Replay i2v backend: serves pre-rendered clips instead of rendering.

For developing and testing the player and the live loop without a GPU.
Each `synthesize` call returns the next `*.mp4` from a directory (cycling
when it runs out), after an optional delay that imitates render latency.
The prompt is ignored; the clips are whatever an earlier live run left in
`logs/video`.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.i2v.base import I2VBackend

logger = logging.getLogger(__name__)


class ReplayI2V(I2VBackend):
    manages_continuity = True  # depends only on the prompt: the loop may pipeline it

    def __init__(
        self,
        *,
        clip_dir: Path | str,
        output_dir: Path | str = "logs/video",
        delay: float = 0.0,
    ) -> None:
        self.clip_dir = Path(clip_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.delay = max(0.0, delay)
        self.clips = sorted(p for p in self.clip_dir.glob("*.mp4") if p.is_file())
        if not self.clips:
            raise ValueError(f"replay backend: no *.mp4 clips in {self.clip_dir}")
        self._next = 0

    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
        duration: int | None = None,
    ) -> str | None:
        if self.delay:
            await asyncio.sleep(self.delay)
        src = self.clips[self._next % len(self.clips)]
        self._next += 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.output_dir / f"turn_{turn:04d}_{stamp}.mp4"
        try:
            if src.resolve() != out.resolve():
                shutil.copyfile(src, out)
        except OSError:
            logger.exception("replay backend: could not copy %s", src)
            return None
        logger.info("replay turn %d → %s (from %s)", turn, out, src.name)
        return str(out)
