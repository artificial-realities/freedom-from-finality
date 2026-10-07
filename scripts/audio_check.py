"""Audio check: each clip's level and the seams of the played mix, offline,
against the audio criteria of the decision record's X-02 and review 04
§9.1. Run it after touching the server's audio encode, the player's
audio handling or how speech is compiled (RUN-13).

Per clip (ffmpeg's ebur128): integrated loudness and true peak; the audio
track shorter than the video; leading and trailing digital silence.

Then the *played mix*: the offline reproduction of what the player outputs
from the clip files (review 04 §9.1), under a seam policy:

  today     each clip starts at the previous clip's video end with a
            250 ms linear fade-in, as `player.html` does now;
  overlap   the next clip starts `--overlap` seconds before the previous
            clip's end and the two are crossfaded with equal power. This is
            the AUD-02 candidate, an evaluation option for X-02 and X-13
            only; it is not what the player does.

On the mix: the level step across each seam (100 ms each side of the
transition; median and p90), onset clicks (a jump at the join sample of
-30 dBFS or more that is at least four times the signal's own sample
steps over the next 20 ms, so a loud continuous track is not a click but
a start from silence into one is), digital-silence holes (200 ms or longer
below -70 dBFS; count and per minute), the spoken-to-next loudness step
when the run's `events.jsonl` names the clips with speech (their
`dialogue`, or a run's voice-over lines from before the narrator was
removed), clips above the ceiling and true-peak overs.

Not here: speech word recall and rate (ASR) and speaker identity
(embeddings), which need faster-whisper and Resemblyzer; they stay the
tooling of X-13 and X-15 in the review's own environment.

    python scripts/audio_check.py logs/live-box/video --run 20260918 \\
        --events logs/live-box/sessions/director-ltxv_live_20260918_145552/events.jsonl
    python scripts/audio_check.py logs/live-box/video --run 20260918 --policy overlap --overlap 0.375

Results go to <out>/audio_check.json (default logs/audio_check/<label>/).
Exit status is 1 when a criterion fails. The thresholds are the record's,
kept here and printed with each row; `baseline.json`'s gates are RUN-04's
list and are not extended by this script.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SR = 48000
FADE_IN_S = 0.25                # the player's fade-in today (player.html: fadeAudio 250 ms)
STEP_SIDE_S = 0.1               # the level step is measured 100 ms each side of the transition
CLICK_DBFS = -30.0              # a click is a jump at the join of at least this
CLICK_RATIO = 4.0               # ... and at least this many times the signal's own sample steps
CLICK_CONTEXT_S = 0.02          # ... measured over this much of the incoming signal
SILENCE_DBFS = -60.0            # leading / trailing silence per clip (review 04)
HOLE_DBFS = -70.0               # a hole in the mix
HOLE_MIN_S = 0.2
FLOOR_DB = -100.0               # levels are floored here so steps stay finite

# The criteria (record X-02; review 04 §9.1). `ceiling` is X-13's to choose; -18 LUFS is the candidate.
CRITERIA = {
    "seam_step_median_db": {"max": 12.0, "source": "X-02"},
    "seam_step_p90_db": {"max": 25.0, "source": "X-02"},
    "onset_clicks": {"max": 0, "source": "X-02"},
    "holes_per_minute": {"max": 0.0, "source": "review 04 §9.1 (room tone on; AUD-09 is deferred)"},
    "clips_above_ceiling": {"max": 0, "source": "review 04 §9.1 (ceiling from X-13)"},
    "true_peak_overs": {"max": 0, "source": "review 04 §9.1 (-1 dBTP)"},
    "spoken_step_median_lu": {"max": 14.0, "source": "review 04 §9.1"},
}
TRUE_PEAK_MAX_DBTP = -1.0


# ── Clips ────────────────────────────────────────────────────────────

def find_clips(directory: Path, run: str | None = None) -> list[tuple[int, Path]]:
    """(turn, path) for a run's clips, sorted by turn: `turn_NNNN_<run>_*.mp4`
    files, or every mp4 in name order when none carries a turn number."""
    files = sorted(Path(directory).glob("*.mp4"))
    turned = []
    for f in files:
        m = re.match(r"turn_(\d+)_(\d{8})_", f.name)
        if m and (run is None or m.group(2) == run):
            turned.append((int(m.group(1)), f))
    if turned:
        return sorted(turned)
    if run is not None:
        return []
    return [(i + 1, f) for i, f in enumerate(files)]


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    info = {"video_s": None, "audio_s": None}
    for s in json.loads(out).get("streams", []):
        key = "video_s" if s.get("codec_type") == "video" else "audio_s" if s.get("codec_type") == "audio" else None
        if key and s.get("duration"):
            info[key] = float(s["duration"])
    return info


def pcm(path: Path, sr: int = SR):
    """The clip's audio as float32 mono at `sr` (empty when it has none)."""
    import numpy as np
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).copy()


def ebur128(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) from ffmpeg's ebur128 summary;
    silence gives (-70.0, -inf)."""
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = log[log.rfind("Summary:"):]
    lufs = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)
    return (float(lufs.group(1)) if lufs else float("nan"), float(peak.group(1)) if peak else float("nan"))


# ── Signal helpers ───────────────────────────────────────────────────

def db(x: float) -> float:
    import math
    return max(FLOOR_DB, 20.0 * math.log10(x)) if x > 0 else FLOOR_DB


def rms_db(x) -> float:
    import numpy as np
    return db(float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))) if len(x) else FLOOR_DB


def silence_edges(x, sr: int = SR, floor_db: float = SILENCE_DBFS, win_s: float = 0.01) -> tuple[float, float]:
    """(leading, trailing) seconds below `floor_db`, in `win_s` RMS windows."""
    import numpy as np
    w = max(1, int(sr * win_s))
    n = len(x) // w
    if n == 0:
        return 0.0, 0.0
    env = np.sqrt(np.mean(x[: n * w].reshape(n, w).astype(np.float64) ** 2, axis=1))
    loud = np.where(env > 10 ** (floor_db / 20))[0]
    if len(loud) == 0:
        return n * w / sr, n * w / sr
    return float(loud[0] * w / sr), float((n - 1 - loud[-1]) * w / sr + (len(x) - n * w) / sr)


def holes(y, sr: int = SR, *, floor_db: float = HOLE_DBFS, min_s: float = HOLE_MIN_S, win_s: float = 0.01) -> list[dict]:
    """Runs of at least `min_s` below `floor_db` in the mix, as (start, seconds)."""
    import numpy as np
    w = max(1, int(sr * win_s))
    n = len(y) // w
    if n == 0:
        return []
    env = np.sqrt(np.mean(y[: n * w].reshape(n, w).astype(np.float64) ** 2, axis=1))
    quiet = env <= 10 ** (floor_db / 20)
    out, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * w / sr >= min_s:
                out.append({"start_s": round(start * w / sr, 3), "seconds": round((i - start) * w / sr, 3)})
            start = None
    return out


# ── The played mix ───────────────────────────────────────────────────

def mix(tracks: list[tuple], *, policy: str = "today", overlap_s: float = 0.375, fade_in_s: float = FADE_IN_S,
        sr: int = SR):
    """`tracks` are (audio, video_seconds) per clip in order. Returns the
    mix and one transition per seam: (start, end) in seconds, the window
    over which the join happens (the fade-in under `today`, the crossfade
    under `overlap`)."""
    import numpy as np
    if policy not in ("today", "overlap"):
        raise ValueError(f"unknown policy {policy!r}")
    starts, t = [], 0.0
    for i, (_x, video_s) in enumerate(tracks):
        if i == 0:
            starts.append(0.0)
        elif policy == "today":
            starts.append(t)
        else:
            starts.append(max(0.0, t - overlap_s))
        t = starts[-1] + video_s
    total = max(s + len(x) / sr for s, (x, _v) in zip(starts, tracks))
    y = np.zeros(int(total * sr) + sr, np.float64)
    transitions = []
    for i, ((x, video_s), start) in enumerate(zip(tracks, starts)):
        x = x.astype(np.float64)
        gain = np.ones(len(x))
        if i > 0 and policy == "today" and fade_in_s > 0:
            n = min(len(x), int(fade_in_s * sr))
            gain[:n] = np.linspace(0.0, 1.0, n, endpoint=False)
            transitions.append((start, start + fade_in_s))
        elif i > 0 and policy == "overlap":
            n = min(len(x), int(overlap_s * sr))
            u = np.linspace(0.0, 1.0, n, endpoint=False)
            gain[:n] = np.sin(u * np.pi / 2)                      # in, equal power
            prev_x, prev_start = tracks[i - 1][0], starts[i - 1]
            a = int((start - prev_start) * sr)                     # where the crossfade starts in the previous clip
            m = min(n, max(0, len(prev_x) - a))
            if m > 0:
                out_gain = np.cos(np.linspace(0.0, 1.0, n, endpoint=False)[:m] * np.pi / 2)
                seg = slice(int(prev_start * sr) + a, int(prev_start * sr) + a + m)
                y[seg] -= prev_x[a: a + m].astype(np.float64) * (1.0 - out_gain)
            transitions.append((start, start + overlap_s))
        elif i > 0:
            transitions.append((start, start))
        s0 = int(start * sr)
        y[s0: s0 + len(x)] += x * gain
    return y[: int(total * sr)].astype(np.float32), transitions


def seam_metrics(y, transitions: list[tuple[float, float]], sr: int = SR) -> list[dict]:
    """Per transition: the level step from the 100 ms before it to the
    100 ms after it, and the jump at the join sample (a click when it is
    both loud and out of scale with the signal's own sample steps)."""
    import numpy as np
    rows = []
    side = int(STEP_SIDE_S * sr)
    for start, end in transitions:
        a, b = int(start * sr), int(end * sr)
        before = y[max(0, a - side): a]
        after = y[b: b + side]
        jump = float(abs(y[a] - y[a - 1])) if 0 < a < len(y) else 0.0
        steps = np.abs(np.diff(y[a: a + int(CLICK_CONTEXT_S * sr)]))
        typical = float(np.median(steps)) if len(steps) else 0.0
        click = db(jump) if jump >= CLICK_RATIO * typical else FLOOR_DB
        rows.append({"start_s": round(start, 3), "before_db": round(rms_db(before), 1), "after_db": round(rms_db(after), 1),
                     "step_db": round(abs(rms_db(after) - rms_db(before)), 1), "click_db": round(click, 1)})
    return rows


# ── The run ──────────────────────────────────────────────────────────

def speech(events_path: Path | None) -> dict[int, str]:
    """What was said in each clip with speech, by segment: its dialogue
    lines' words, or (a run from before 2026-10-06) its voice-over."""
    if events_path is None:
        return {}
    out = {}
    for line in Path(events_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            said = " ".join(d.partition(": ")[2] for d in rec.get("dialogue") or []) or rec.get("voiceover", "")
            if said:
                out[int(rec["segment"])] = said
    return out


def check(clips: list[tuple[int, Path]], *, policy: str = "today", overlap_s: float = 0.375, fade_in_s: float = FADE_IN_S,
          ceiling_lufs: float = -18.0, events: Path | None = None, click_dbfs: float = CLICK_DBFS) -> dict:
    import numpy as np
    spoken = speech(events)
    per_clip, tracks = [], []
    for turn, path in clips:
        info = probe(path)
        x = pcm(path)
        lufs, peak = ebur128(path)
        lead, trail = silence_edges(x)
        video_s = info["video_s"] or len(x) / SR
        per_clip.append({"turn": turn, "file": path.name, "lufs": lufs, "true_peak_dbtp": peak,
                         "video_s": round(video_s, 4), "audio_s": round(len(x) / SR, 4),
                         "audio_short_ms": round((video_s - len(x) / SR) * 1000, 1),
                         "leading_silence_s": round(lead, 3), "trailing_silence_s": round(trail, 3),
                         "spoken": turn in spoken, "words": len(spoken.get(turn, "").split())})
        tracks.append((x, video_s))
    y, transitions = mix(tracks, policy=policy, overlap_s=overlap_s, fade_in_s=fade_in_s)
    seams = seam_metrics(y, transitions)
    for row, (a, b) in zip(seams, zip(clips, clips[1:])):
        row["from"], row["to"] = a[0], b[0]
    steps = sorted(r["step_db"] for r in seams)
    clicks = [r for r in seams if r["click_db"] >= click_dbfs]
    found = holes(y)
    minutes = len(y) / SR / 60.0
    by_turn = {c["turn"]: c for c in per_clip}
    spoken_steps = [by_turn[t]["lufs"] - by_turn[t + 1]["lufs"] for t in sorted(spoken)
                      if t in by_turn and t + 1 in by_turn and np.isfinite(by_turn[t]["lufs"]) and np.isfinite(by_turn[t + 1]["lufs"])]
    metrics = {
        "clips": len(per_clip), "seams": len(seams), "policy": policy,
        "overlap_s": overlap_s if policy == "overlap" else 0.0, "fade_in_s": fade_in_s if policy == "today" else 0.0,
        "mix_minutes": round(minutes, 3),
        "seam_step_median_db": _pct(steps, 50), "seam_step_p90_db": _pct(steps, 90),
        "onset_clicks": len(clicks), "clicks_at": [r["from"] for r in clicks],
        "holes": len(found), "holes_per_minute": round(len(found) / minutes, 3) if minutes else None,
        "holes_seconds": round(sum(h["seconds"] for h in found), 3),
        "clips_above_ceiling": sum(1 for c in per_clip if c["lufs"] > ceiling_lufs), "ceiling_lufs": ceiling_lufs,
        "true_peak_overs": sum(1 for c in per_clip if c["true_peak_dbtp"] > TRUE_PEAK_MAX_DBTP),
        "spoken_clips": sum(1 for c in per_clip if c["spoken"]),
        "spoken_step_median_lu": _pct(sorted(spoken_steps), 50) if spoken_steps else None,
        "trailing_silence_clips": sum(1 for c in per_clip if c["trailing_silence_s"] >= HOLE_MIN_S),
        "audio_short_ms_median": _pct(sorted(c["audio_short_ms"] for c in per_clip), 50),
    }
    return {"metrics": metrics, "criteria": criteria_rows(metrics), "clips_detail": per_clip, "seams_detail": seams,
            "holes_detail": found}


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    pos = (len(xs) - 1) * q / 100.0
    lo, hi = int(pos), min(int(pos) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo), 2)


def criteria_rows(metrics: dict) -> list[dict]:
    rows = []
    for name, spec in CRITERIA.items():
        value = metrics.get(name)
        if value is None:
            rows.append({"criterion": name, "value": None, "threshold": spec["max"], "result": "not measured",
                         "source": spec["source"]})
        else:
            rows.append({"criterion": name, "value": value, "threshold": spec["max"],
                         "result": "PASS" if value <= spec["max"] else "FAIL", "source": spec["source"]})
    return rows


# ── Main ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", type=Path, help="directory of a run's clips (turn_NNNN_<run>_*.mp4)")
    ap.add_argument("--run", help="the run date in the file names, when the directory holds several")
    ap.add_argument("--events", type=Path, default=None, help="the run's events.jsonl, for the clips with speech")
    ap.add_argument("--first", type=int, default=None, help="start at this turn")
    ap.add_argument("--count", type=int, default=None, help="how many consecutive clips (default: all)")
    ap.add_argument("--policy", choices=("today", "overlap"), default="today")
    ap.add_argument("--overlap", type=float, default=0.375, help="crossfade seconds under --policy overlap")
    ap.add_argument("--fade-in", type=float, default=FADE_IN_S, help="the player's fade-in under --policy today")
    ap.add_argument("--ceiling", type=float, default=-18.0, help="LUFS ceiling a clip may not exceed (X-13's value)")
    ap.add_argument("--label", default=time.strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--out", type=Path, default=None, help="default logs/audio_check/<label>")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    clips = find_clips(args.clips, args.run)
    if args.first is not None:
        clips = [c for c in clips if c[0] >= args.first]
    if args.count is not None:
        clips = clips[: args.count]
    if len(clips) < 2:
        print(f"need at least two clips; found {len(clips)} in {args.clips}", file=sys.stderr)
        return 2
    result = check(clips, policy=args.policy, overlap_s=args.overlap, fade_in_s=args.fade_in,
                   ceiling_lufs=args.ceiling, events=args.events)
    result["label"] = args.label
    result["source"] = {"clips": str(args.clips), "run": args.run, "events": str(args.events) if args.events else None,
                        "turns": [t for t, _ in clips]}
    out = args.out or (REPO / "logs" / "audio_check" / args.label)
    out.mkdir(parents=True, exist_ok=True)
    (out / "audio_check.json").write_text(json.dumps(result, indent=1, default=str))

    if args.json:
        print(json.dumps(result["metrics"], indent=1, default=str))
    else:
        m = result["metrics"]
        print(f"Audio check {args.label}: {m['clips']} clips ({clips[0][0]}-{clips[-1][0]}), policy {m['policy']}"
              + (f" (overlap {m['overlap_s']} s)" if m["policy"] == "overlap" else f" (fade-in {m['fade_in_s']} s)"))
        print(f"  clips: {m['clips_above_ceiling']} above {m['ceiling_lufs']} LUFS, {m['true_peak_overs']} over "
              f"{TRUE_PEAK_MAX_DBTP} dBTP, {m['trailing_silence_clips']} end in ≥ {HOLE_MIN_S} s of silence, "
              f"audio shorter than video by {m['audio_short_ms_median']} ms (median); {m['spoken_clips']} with speech")
        print(f"  mix: {m['mix_minutes']} min; seam step median {m['seam_step_median_db']} dB, p90 {m['seam_step_p90_db']} dB; "
              f"clicks {m['onset_clicks']}{' at ' + str(m['clicks_at']) if m['clicks_at'] else ''}; "
              f"holes {m['holes']} ({m['holes_per_minute']} per minute, {m['holes_seconds']} s)"
              + (f"; spoken-to-next step median {m['spoken_step_median_lu']} LU" if m["spoken_step_median_lu"] is not None else ""))
        for row in result["criteria"]:
            value = "—" if row["value"] is None else row["value"]
            print(f"  {row['result']:<12} {row['criterion']:<26} {value} (≤ {row['threshold']}; {row['source']})")
        print(f"  written to {out}")
    return 1 if any(r["result"] == "FAIL" for r in result["criteria"]) else 0


if __name__ == "__main__":
    sys.exit(main())
