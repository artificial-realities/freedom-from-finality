"""GPU session 4's measurements, after the pull.

    python scripts/experiments/session4_analyse.py --out logs/experiments/gpu4 [--only run17,ltx15,live]

run17  per arm: render wall per clip; a sheet per seed with the 640x352 arm
       above the 512x288 arms (clips 1, 4, 8, 12; frames 0/72/143 each,
       all shown at 512x288) for the by-eye identity, framing and detail
       call; the figure's height and centre in each clip's first frame.
ltx15  per clip: hard cuts (a frame-to-frame change far above the clip's
       own, as in session 3) and where they fall; whether the clip written
       with a cut has one and the others none; a sheet per arm.
live   per film (logs/live-box/session4a, session4b): the clips that
       carried a cut (the transcript's "Cut to"), their detected cuts, and
       a sheet of them; `bench_report` and `seam_check` run separately.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.experiments.session3_analyse import _clips, _cuts, _frames, _save  # noqa: E402

SHOW = (512, 288)


def _row(clip: Path, idx=(0, 72, 143)) -> np.ndarray:
    f = _frames(clip, SHOW)
    return np.concatenate([f[min(i, len(f) - 1)] for i in idx], axis=1)


def _figure(frame: np.ndarray) -> dict:
    mask = frame.min(axis=2) < 200
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return {"height": None, "centre_x": None}
    return {"height": round((ys.max() - ys.min()) / mask.shape[0], 3), "centre_x": round(xs.mean() / mask.shape[1], 3)}


def run17(out: Path) -> dict:
    base = out / "run17"
    report = {}
    for arm in sorted(p for p in base.iterdir() if p.is_dir()):
        rows = [json.loads(line) for line in (arm / "renders.jsonl").read_text().splitlines()] if (arm / "renders.jsonl").exists() else []
        clips = _clips(arm)
        if not clips:
            continue
        walls = [r["wall_s"] for r in rows[1:]]
        report[arm.name] = {
            "clips": len(clips), "wall_median": round(statistics.median(walls), 3) if walls else None,
            "wall_max": max(walls) if walls else None,
            "figure_first_frame": [_figure(_frames(c, SHOW)[0]) for c in clips[:1] + clips[3:4] + clips[7:8] + clips[11:12]],
        }
    for seed in (0, 1):
        arms = [a for a in (f"a-640-square-s{seed}", f"b-512-16x9-s{seed}", f"c-512-square-s{seed}") if (base / a).is_dir() and _clips(base / a)]
        blocks = []
        for arm in arms:
            clips = _clips(base / arm)
            blocks.append(np.concatenate([_row(clips[i]) for i in (0, 3, 7, 11) if i < len(clips)], axis=0))
        if blocks:
            gap = np.full((blocks[0].shape[0], 16, 3), 0, np.uint8)
            _save(np.concatenate(sum(([b, gap] for b in blocks), [])[:-1], axis=1), base / f"sheet-s{seed}.png")
    (base / "run17.json").write_text(json.dumps(report, indent=1))
    for k, v in report.items():
        print(f"run17 {k}: wall median {v['wall_median']} max {v['wall_max']}; figure {v['figure_first_frame'][0]}")
    return report


def ltx15(out: Path) -> dict:
    base = out / "ltx15"
    prompts = json.loads((base / "prompts-cut.json").read_text())
    written = [i for i, p in enumerate(prompts) if "The view cuts to" in p]
    report = {}
    for arm in sorted(p for p in base.iterdir() if p.is_dir()):
        clips = _clips(arm)
        per = []
        for i, c in enumerate(clips):
            f = _frames(c)
            per.append({"clip": i + 1, "cuts_at": _cuts(f), "frames": len(f), "written": i in written and arm.name.startswith("cut")})
        report[arm.name] = per
        _save(np.concatenate([_row(c, (0, 24, 48, 96, 143)) for c in clips], axis=0), arm / "sheet.png")
        hits = sum(1 for p in per if p["written"] and p["cuts_at"])
        extra = sum(1 for p in per if not p["written"] and p["cuts_at"])
        print(f"ltx15 {arm.name}: written cuts detected {hits}/{sum(p['written'] for p in per)}, "
              f"cuts where none written {extra}; " + ", ".join(f"{p['clip']}:{p['cuts_at']}" for p in per if p["cuts_at"]))
    (base / "cuts.json").write_text(json.dumps(report, indent=1))
    return report


def live(out: Path) -> dict:
    report = {}
    for tag in ("session4a", "session4b"):
        root = REPO / "logs" / "live-box" / tag
        events = next(root.glob("sessions/*/events.jsonl"), None)
        if events is None:
            continue
        ev = [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
        cut_segments = [e["segment"] for e in ev if str(e.get("narration", "")).startswith("Cut to")]
        clips = {int(m.group(1)): p for p in root.glob("video/turn_*.mp4") if (m := re.match(r"turn_(\d+)_", p.name))}
        rows, sheet = [], []
        for seg in cut_segments:
            clip = clips.get(seg)
            if clip is None:
                continue
            f = _frames(clip)
            rows.append({"segment": seg, "cuts_at": _cuts(f), "narration": next(e["narration"] for e in ev if e["segment"] == seg)[:120]})
            sheet.append(_row(clip, (0, 24, 48, 96, 143)))
        if sheet:
            _save(np.concatenate(sheet, axis=0), root / "cuts-sheet.png")
        report[tag] = {"clips": len(ev), "cut_clips": len(cut_segments), "rows": rows}
        print(f"live {tag}: {len(ev)} clips, {len(cut_segments)} written cuts, detected in "
              f"{sum(1 for r in rows if r['cuts_at'])} of {len(rows)} found clips")
    (out / "live-cuts.json").write_text(json.dumps(report, indent=1))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "gpu4")
    ap.add_argument("--only", default="run17,ltx15,live")
    args = ap.parse_args(argv)
    for name in args.only.split(","):
        {"run17": run17, "ltx15": ltx15, "live": live}[name](args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
