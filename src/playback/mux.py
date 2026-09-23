"""ffmpeg audio→video mux for Artificial Realities playback.

LTX-2.5 clips arrive with a native ambient/SFX track. When
a TTS voice-over needs to be layered on top, the mux *mixes*
the voiceover over the existing audio (ducked) rather than replacing
it — see `has_audio_stream`.

The `ffmpeg` binary must be on PATH. If it's missing or the mux
fails, we fail soft and the caller falls back to playing the silent
(or ambient-only) video.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# How far to duck a clip's native ambient track under the voiceover
# when mixing the two. Keeps the bed audible without fighting speech.
AMBIENT_DUCK_GAIN = 0.25


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def has_audio_stream(path: Path | str) -> bool:
    """True when `path` already carries an audio stream.

    LTX-2.5 renders native audio alongside video. Fails closed (False)
    when ffprobe is unavailable, so the caller falls back to a plain
    replace-mux.
    """
    if not shutil.which("ffprobe"):
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(path),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except Exception:
        logger.debug("ffprobe audio-stream check failed for %s", path)
        return False
    return proc.returncode == 0 and bool(stdout.strip())


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
    # The video is copied as-is; audio is re-encoded to AAC because
    # mp3-in-mp4 is not universally supported by ffplay/QuickTime.
    if await has_audio_stream(video):
        # The clip already has a native track (LTX-2.5 ambient/SFX).
        # Duck it under the voiceover and mix, rather than dropping it:
        # replacing would silently discard everything the model
        # generated. `duration=first` keeps the mix as long as the
        # ducked ambient bed so a short line doesn't cut the clip.
        cmd = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-i", str(video),
            "-i", str(audio),
            "-filter_complex",
            f"[0:a]volume={AMBIENT_DUCK_GAIN}[bed];"
            "[1:a]volume=1.0[vo];"
            "[bed][vo]amix=inputs=2:duration=first:dropout_transition=0"
            "[aout]",
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            str(out),
        ]
    else:
        # Silent clip — map the voiceover straight in.
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
