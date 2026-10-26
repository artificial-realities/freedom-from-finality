"""Media helpers on ffmpeg: a rendered clip's real length, checked against
its clip shape, and the still of the frames the story stopped on."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def is_ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


async def probe_duration(path: str) -> float | None:
    """Return duration in seconds, or None if ffprobe is missing or fails."""
    if not is_ffprobe_available():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode().strip()
        if not text:
            return None
        return float(text)
    except Exception:
        logger.exception("ffprobe failed for %s", path)
        return None


def last_frame_png(video: Path | str) -> bytes:
    """The video's last frame as PNG bytes, without writing a file: cheap
    for the few frames a saved state keeps (they are reversed to reach it)."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vf", "reverse", "-frames:v", "1",
                           "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
