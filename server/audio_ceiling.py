"""The loudness ceiling for a clip's audio, applied by the server. Stdlib
and ffmpeg only, so the GPU box and a laptop compute the same numbers.

- pad the audio to the video's length (the 32 ms hole at every clip end);
- measure the integrated loudness and the true peak (ffmpeg's ebur128,
  ITU-R BS.1770);
- apply one static gain that never boosts: down to the loudness ceiling
  if the clip is louder than it, and further down if the true peak would
  still be above the limit (a static trim, not a dynamic limiter, so
  quiet clips and silence are untouched and nothing pumps).

The server applies it only with LTX2_AUDIO_CEILING_LUFS set; the live
config sets -18 with the overlap seam, so the sound crossfades between
clips of the same loudness.
"""

from __future__ import annotations

import re
import subprocess

TRUE_PEAK_LIMIT_DB = -1.0


def measure(path: str) -> tuple[float | None, float | None]:
    """(integrated loudness in LUFS, true peak in dBTP) of the audio in
    `path`; None where ffmpeg finds nothing to measure (silence gives
    -70 LUFS or below, which is returned as measured)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = proc.stderr
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text
    loud = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)

    def num(m):
        if m is None:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))

    return num(loud), num(peak)


def gain_db(lufs: float | None, true_peak: float | None, ceiling: float,
            peak_limit: float = TRUE_PEAK_LIMIT_DB) -> float:
    """The one static gain for a clip: never above 0 dB; down to `ceiling`
    when louder than it; down further so the true peak stays at or below
    `peak_limit`."""
    gain = 0.0
    if lufs is not None and lufs != float("-inf") and lufs > ceiling:
        gain = ceiling - lufs
    if true_peak is not None and true_peak != float("-inf") and true_peak + gain > peak_limit:
        gain = peak_limit - true_peak
    return round(min(0.0, gain), 2)


def audio_filter(gain: float, video_seconds: float | None) -> str:
    """The ffmpeg audio filter that pads to the video and applies the gain."""
    parts = []
    if video_seconds:
        parts.append(f"apad=whole_dur={video_seconds:.4f}")
    parts.append(f"volume={gain:.2f}dB")
    return ",".join(parts)
