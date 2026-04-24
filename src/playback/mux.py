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


async def concat_videos_and_mux_audio(
    *,
    video_paths: list[Path | str],
    audio_path: Path | str,
    output_path: Path | str,
) -> str | None:
    """Concatenate `video_paths` in order and overlay `audio_path` onto the
    result. Returns the output path on success, None on any failure.

    Used for Attenborough's spanning commentary: one voiceover plays
    over multiple back-to-back clips. The single audio is muxed across
    the whole concatenated unit so the player sees one continuous file.
    """
    if not video_paths:
        return None
    if len(video_paths) == 1:
        # Single-clip span — same as plain mux.
        return await mux_audio_into_video(
            video_path=video_paths[0],
            audio_path=audio_path,
            output_path=output_path,
        )

    audio = Path(audio_path)
    out = Path(output_path)
    videos = [Path(p) for p in video_paths]

    if not audio.exists():
        logger.warning("Concat+mux skipped — audio missing: %s", audio)
        return None
    for v in videos:
        if not v.exists():
            logger.warning("Concat+mux skipped — video missing: %s", v)
            return None
    if not is_ffmpeg_available():
        logger.warning("Concat+mux skipped — ffmpeg not on PATH")
        return None

    out.parent.mkdir(parents=True, exist_ok=True)

    # Build a filter_complex that concats N video streams and maps the
    # external audio. Single-pass; no temp file.
    n = len(videos)
    filter_chain = (
        "".join(f"[{i}:v:0]" for i in range(n))
        + f"concat=n={n}:v=1:a=0[v]"
    )
    audio_input_idx = n  # the audio is the (n)-th input

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for v in videos:
        cmd.extend(["-i", str(v)])
    cmd.extend(["-i", str(audio)])
    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[v]",
        "-map", f"{audio_input_idx}:a:0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        str(out),
    ])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except Exception as exc:
        logger.exception("ffmpeg concat+mux failed to launch: %s", exc)
        return None

    if proc.returncode != 0:
        logger.warning("ffmpeg concat+mux exited %s: %s", proc.returncode,
                       stderr.decode("utf-8", errors="replace")[:500])
        return None

    return str(out)
