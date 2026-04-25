"""Media duration probing via ffprobe.

Used to bookkeep audio-vs-video lengths so the live producer knows when
a voiceover spills past the current clip and how many subsequent silent
clips it should concatenate behind it.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

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
