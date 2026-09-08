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


async def concat_videos_and_mux_audio(
    *,
    video_paths: list[Path | str],
    audio_path: Path | str,
    output_path: Path | str,
) -> str | None:
    """Concatenate `video_paths` in order and overlay `audio_path` onto the
    result. Returns the output path on success, None on any failure.

    Used for a spanning voice-over: one line plays
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
    audio_input_idx = n  # the voiceover is the (n)-th input

    # When the clips carry native audio (LTX-2.5 ambient/SFX), concat
    # that bed too and duck it under the voiceover instead of dropping
    # it. Mixed clips are all-or-nothing: if any clip in the span is
    # silent, concat with a=1 would fail on mismatched stream counts,
    # so fall back to the video-only concat.
    beds = [await has_audio_stream(v) for v in videos]
    mix_ambient = all(beds)

    if mix_ambient:
        filter_chain = (
            "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
            + f"concat=n={n}:v=1:a=1[v][bed];"
            + f"[bed]volume={AMBIENT_DUCK_GAIN}[ducked];"
            + f"[{audio_input_idx}:a:0]volume=1.0[vo];"
            + "[ducked][vo]amix=inputs=2:duration=first:"
            + "dropout_transition=0[aout]"
        )
        audio_map = "[aout]"
    else:
        filter_chain = (
            "".join(f"[{i}:v:0]" for i in range(n))
            + f"concat=n={n}:v=1:a=0[v]"
        )
        audio_map = f"{audio_input_idx}:a:0"

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for v in videos:
        cmd.extend(["-i", str(v)])
    cmd.extend(["-i", str(audio)])
    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[v]",
        "-map", audio_map,
        "-c:a", "aac",
        "-b:a", "192k" if mix_ambient else "128k",
    ])
    if not mix_ambient:
        # amix already clamps to the ducked bed; -shortest would fight it.
        cmd.append("-shortest")
    cmd.append(str(out))

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
