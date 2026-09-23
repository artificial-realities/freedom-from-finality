"""Apply AUD-03's candidate audio treatment (server/audio_ceiling.py) to a
run's clips, offline, for X-13 (which ceiling) and X-02 (the seam design's
audio): the audio padded to the video, one static non-boosting gain down
to the ceiling and under a -1 dBTP true peak. The video is copied; the
audio is re-encoded as the server would (AAC 192k).

    python scripts/experiments/ceiling.py logs/live-box/video --run 20260918 --ceiling -18 \\
        --out logs/experiments/x13/ceiling-18

Writes the processed clips under the same names (so scripts/audio_check.py
reads them as a run) and ceiling.json with every clip's loudness, true
peak and gain before and after.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location("audio_ceiling", REPO / "server" / "audio_ceiling.py")
audio_ceiling = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio_ceiling)

_seam = importlib.util.spec_from_file_location("seam_check", REPO / "scripts" / "seam_check.py")
seam_check = importlib.util.module_from_spec(_seam)
_seam.loader.exec_module(seam_check)


def process(src: Path, dst: Path, ceiling: float) -> dict:
    video_s = seam_check.probe(src)["video_s"]
    lufs, peak = audio_ceiling.measure(str(src))
    gain = audio_ceiling.gain_db(lufs, peak, ceiling)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0",
                    "-c:v", "copy", "-af", audio_ceiling.audio_filter(gain, video_s),
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{video_s:.4f}", str(dst)], check=True)
    after_lufs, after_peak = audio_ceiling.measure(str(dst))
    return {"clip": src.name, "video_s": video_s, "lufs": lufs, "true_peak": peak, "gain_db": gain,
            "after_lufs": after_lufs, "after_true_peak": after_peak}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", type=Path)
    ap.add_argument("--run", help="the run date in the file names")
    ap.add_argument("--ceiling", type=float, required=True, help="loudness ceiling in LUFS (X-13: -18 or -20)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for turn, path in seam_check.find_clips(args.clips, args.run):
        rows.append({"turn": turn, **process(path, args.out / path.name, args.ceiling)})
        r = rows[-1]
        print(f"{r['turn']:3} {r['lufs']!s:>7} LUFS {r['true_peak']!s:>6} dBTP  gain {r['gain_db']:6.2f} dB  "
              f"→ {r['after_lufs']!s:>7} LUFS {r['after_true_peak']!s:>6} dBTP", flush=True)
    (args.out / "ceiling.json").write_text(json.dumps({"ceiling": args.ceiling, "clips": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
