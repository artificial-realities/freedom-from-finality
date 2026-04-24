"""Pop-up video player via ffplay subprocess.

ffplay is part of the ffmpeg distribution. We launch one process per
clip and await its exit. `-autoexit` closes the window when the clip
ends; `-window_title` keeps the demo identifiable on the user's
desktop. If ffplay is missing the player fails soft — clips are still
generated and logged, just not displayed.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def is_ffplay_available() -> bool:
    return shutil.which("ffplay") is not None


async def play_clip(
    path: str,
    *,
    window_title: str = "ClankerStudios",
    extra_args: list[str] | None = None,
) -> int:
    """Play `path` to completion via ffplay. Returns the process exit code.

    Returns -1 if ffplay is not available or could not be launched.
    """
    if not is_ffplay_available():
        logger.warning("ffplay not on PATH — skipping playback of %s", path)
        return -1

    cmd = [
        "ffplay",
        "-autoexit",
        "-loglevel", "quiet",
        "-window_title", window_title,
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(path)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return await proc.wait()
    except Exception as exc:
        logger.exception("ffplay failed to launch for %s: %s", path, exc)
        return -1
