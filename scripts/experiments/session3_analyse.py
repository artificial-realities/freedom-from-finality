"""GPU session 3's measurements (BATCH_031), after the pull.

    python scripts/experiments/session3_analyse.py --out logs/experiments/gpu3 [--only x22,x23,x31,x12]
    logs/_scratch/review-04/wenv/bin/python scripts/experiments/session3_analyse.py --only x23-asr   # faster-whisper

x22  per wild line, the rules-on and rules-off clips side by side (frames
     0/48/96/144 of each clip) for the by-eye artefact count and whether the
     request shows; the Director's actions next to them (beats.json).
x23  per clip: loudness, spectral flatness (low = tonal: music or a sung
     note), and with --only x23-asr the words faster-whisper hears; clips
     where speech was asked (a quoted line or the narrator) are marked.
x31  per clip: hard cuts, a frame-to-frame change far above the clip's own
     (median × 8 and above 12 on 0-255), where they fall, and whether the
     continuing clip picks up the first clip's last shot (its first frame
     against the first clip's frame n − 10, the overlap cut).
x12  the first clip of each arm and seed: frames 0/48/96/144, the figure's
     height and centre (non-white mask), for the by-eye identity/framing call.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]


def _frames(path: Path, size=(320, 176)) -> np.ndarray:
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


def _clips(folder: Path) -> list[Path]:
    return sorted(folder.glob("clip_*.mp4"))


def _strip(frames: np.ndarray, idx=(0, 48, 96, 144)) -> np.ndarray:
    return np.concatenate([frames[min(i, len(frames) - 1)] for i in idx], axis=1)


def _save(img: np.ndarray, path: Path) -> None:
    from PIL import Image
    Image.fromarray(img.astype(np.uint8)).save(path)


def x22(out: Path) -> dict:
    beats = json.loads((out / "x22" / "beats.json").read_text())
    rows = {}
    for n in range(1, 11):
        pair = []
        for arm in ("on", "off"):
            clips = _clips(out / "x22" / f"{arm}-{n:02d}")
            if clips:
                pair.append(np.concatenate([_strip(_frames(c)) for c in clips], axis=0))
        if len(pair) == 2:
            h = max(p.shape[0] for p in pair)
            pair = [np.pad(p, ((0, h - p.shape[0]), (0, 0), (0, 0)), constant_values=0) for p in pair]
            _save(np.concatenate([pair[0], np.zeros((h, 12, 3)), pair[1]], axis=1), out / "x22" / f"pair-{n:02d}.png")
        rows[n] = {"line": beats.get(f"on-{n:02d}", {}).get("line"),
                   "on": beats.get(f"on-{n:02d}", {}).get("actions"), "off": beats.get(f"off-{n:02d}", {}).get("actions")}
    (out / "x22" / "pairs.json").write_text(json.dumps(rows, indent=1))
    print(f"x22: {len(rows)} pairs → {out / 'x22'}/pair-NN.png")
    return rows


def _audio(path: Path, sr: int = 16000) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def _flatness(x: np.ndarray, n: int = 2048) -> float | None:
    """Mean spectral flatness over the frames with sound (geometric over
    arithmetic mean of the power spectrum): near 1 is noise, near 0 a tone."""
    if len(x) < n:
        return None
    frames = [x[i:i + n] for i in range(0, len(x) - n, n // 2)]
    vals = []
    for f in frames:
        if np.sqrt(np.mean(f ** 2)) < 1e-3:
            continue
        p = np.abs(np.fft.rfft(f * np.hanning(n))) ** 2 + 1e-12
        vals.append(float(np.exp(np.mean(np.log(p))) / np.mean(p)))
    return round(statistics.median(vals), 4) if vals else None


def x23(out: Path) -> dict:
    report = {}
    for arm in ("plain", "clause"):
        spec = json.loads((out / "x23" / arm / "arm.json").read_text())
        rows = []
        for c, clip in zip(spec["chain"], _clips(out / "x23" / arm)):
            x = _audio(clip)
            rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
            rows.append({"segment": c["segment"], "speech_asked": ("says" in c["prompt"] or "narrator" in c["prompt"].lower()),
                         "rms_db": round(20 * np.log10(rms + 1e-9), 1), "flatness": _flatness(x)})
        report[arm] = rows
    (out / "x23" / "audio.json").write_text(json.dumps(report, indent=1))
    for arm, rows in report.items():
        quiet = [r for r in rows if not r["speech_asked"]]
        print(f"x23 {arm}: {len(quiet)} clips without speech asked; median level {statistics.median(r['rms_db'] for r in quiet)} dB, "
              f"median flatness {statistics.median(r['flatness'] or 0 for r in quiet)}")
    return report


def x23_asr(out: Path) -> dict:
    from faster_whisper import WhisperModel
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    report = {}
    for arm in ("plain", "clause"):
        spec = json.loads((out / "x23" / arm / "arm.json").read_text())
        rows = []
        for c, clip in zip(spec["chain"], _clips(out / "x23" / arm)):
            segs, _ = model.transcribe(str(clip), language="en", vad_filter=False, condition_on_previous_text=False)
            segs = list(segs)
            text = " ".join(s.text.strip() for s in segs).strip()
            rows.append({"segment": c["segment"], "speech_asked": ("says" in c["prompt"] or "narrator" in c["prompt"].lower()),
                         "heard": text, "words": len(text.split()),
                         "no_speech": [round(s.no_speech_prob, 2) for s in segs]})
        report[arm] = rows
        quiet = [r for r in rows if not r["speech_asked"]]
        print(f"x23 asr {arm}: words heard in clips without speech asked: {sum(r['words'] for r in quiet)} "
              f"over {len(quiet)} clips; clips with any: {sum(1 for r in quiet if r['words'])}")
    (out / "x23" / "asr.json").write_text(json.dumps(report, indent=1))
    return report


def _cuts(frames: np.ndarray) -> list[int]:
    f = frames.astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2, 3))
    med = float(np.median(d)) or 1e-3
    return [i + 1 for i, v in enumerate(d) if v > max(8 * med, 12.0)]


def x31(out: Path) -> dict:
    report = {}
    for folder in sorted((out / "x31").iterdir()):
        clips = _clips(folder)
        if len(clips) < 2:
            continue
        a, b = _frames(clips[0]), _frames(clips[1])
        join = float(np.abs(a[len(a) - 10].astype(np.float32) - b[0].astype(np.float32)).mean())
        report[folder.name] = {"cuts_clip1": _cuts(a), "cuts_clip2": _cuts(b), "join_next": round(join, 2)}
        _save(np.concatenate([_strip(a, (0, 36, 72, 108, 144)), _strip(b, (0, 36, 72, 108, 144))], axis=0),
              folder / "sheet.png")
    (out / "x31" / "cuts.json").write_text(json.dumps(report, indent=1))
    for kind in ("single", "cut"):
        rows = {k: v for k, v in report.items() if k.startswith(kind)}
        print(f"x31 {kind}: clips with a cut {sum(1 for v in rows.values() if v['cuts_clip1'])} of {len(rows)}; "
              f"continuing clips with a cut {sum(1 for v in rows.values() if v['cuts_clip2'])}; "
              f"join to the next median {statistics.median(v['join_next'] for v in rows.values()) if rows else None}")
    return report


def x12(out: Path) -> dict:
    report, strips = {}, []
    for folder in sorted(p for p in (out / "x12").iterdir() if p.is_dir()):
        clips = _clips(folder)
        if not clips:
            continue
        f = _frames(clips[0])
        mask = f[0].min(axis=2) < 200
        ys, xs = np.nonzero(mask)
        report[folder.name] = {"figure_height": round((ys.max() - ys.min()) / mask.shape[0], 3) if len(ys) else None,
                               "centre_x": round(xs.mean() / mask.shape[1], 3) if len(xs) else None}
        strips.append(np.concatenate([_strip(f), *[_strip(_frames(c)) for c in clips[1:3]]], axis=0))
    if strips:
        _save(np.concatenate(strips, axis=1 if False else 0), out / "x12" / "sheet.png")
    (out / "x12" / "framing.json").write_text(json.dumps(report, indent=1))
    print("x12:", json.dumps(report))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "gpu3")
    ap.add_argument("--only", default="x22,x23,x31,x12")
    args = ap.parse_args(argv)
    for name in args.only.split(","):
        {"x22": x22, "x23": x23, "x23-asr": x23_asr, "x31": x31, "x12": x12}[name](args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
