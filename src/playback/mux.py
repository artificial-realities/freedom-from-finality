"""ffmpeg audio→video mux for ClankerStudios playback.

DashScope clips are silent mp4; ElevenLabs gives us mp3. The player
needs a single file with both streams so a one-shot subprocess can
play the result. We do the mux with `ffmpeg` (subprocess) — it must
be on PATH. If ffmpeg is missing or the mux fails, we fail soft and
the caller falls back to playing the silent video.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def mux_audio_into_video(
    *,
    video_path: Path | str,
    audio_path: Path | str,
    output_path: Path | str,
) -> str | None:
    """Mux `audio_path` onto `video_path` → `output_path`. Returns the
    output path on success, None on any failure (caller falls back).
    """
    video = Path(video_path)
    audio = Path(audio_path)
    out = Path(output_path)

    if not video.exists():
        logger.warning("Mux skipped — video missing: %s", video)
        return None
    if not audio.exists():
        logger.warning("Mux skipped — audio missing: %s", audio)
        return None
    if not is_ffmpeg_available():
        logger.warning("Mux skipped — ffmpeg not on PATH")
        return None

    out.parent.mkdir(parents=True, exist_ok=True)

    # -shortest: clamp to the shorter input so a long audio doesn't
    #   stretch the video silence-padded; a short audio doesn't trim
    #   the video either — the surplus video plays silent.
    # We override -shortest by explicitly mapping streams and copying
    # the video as-is; audio is re-encoded to AAC because mp3-in-mp4
    # is not universally supported by ffplay/QuickTime.
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "error",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        str(out),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except Exception as exc:
        logger.exception("ffmpeg mux failed to launch: %s", exc)
        return None

    if proc.returncode != 0:
        logger.warning("ffmpeg mux exited %s: %s", proc.returncode,
                       stderr.decode("utf-8", errors="replace")[:500])
        return None

    return str(out)
