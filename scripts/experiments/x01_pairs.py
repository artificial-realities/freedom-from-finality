"""X-01's listening material: is the LTX narrator one voice to a listener?

From a run's narrated clips (the speech spans found by review 04's ASR,
`logs/_scratch/review-04/asr.json`), builds 20 cross-clip pairs (two
different clips' narration, 0.6 s apart) and 5 same-clip controls (the two
halves of one clip's line), in a random order under blind names, with the
answer key kept apart and a response sheet for each listener.

    python scripts/experiments/x01_pairs.py logs/live-box/video --run 20260918 --out logs/experiments/x01

Success (decision record §4.1): at least three listeners; >= 90 % of the
cross-clip pairs judged "same"; the controls >= 95 %.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAIRS, CONTROLS, GAP_S = 20, 5, 0.6


def cut(src: Path, start: float, end: float, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
                    "-map", "0:a:0", "-ac", "1", "-ar", "48000", str(dst)], check=True)


def join(a: Path, b: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a), "-i", str(b), "-filter_complex",
                    f"[0:a]apad=pad_dur={GAP_S}[x];[x][1:a]concat=n=2:v=0:a=1", str(dst)], check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", type=Path)
    ap.add_argument("--run", default="20260918")
    ap.add_argument("--asr", type=Path, default=REPO / "logs" / "_scratch" / "review-04" / "asr.json")
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "x01")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    spans = {r["seg"]: (r["first"], r["last"]) for r in json.loads(args.asr.read_text())
             if r.get("ref") and r.get("recall", 0) > 0.5 and r["last"] - r["first"] > 1.5}
    files = {int(p.name.split("_")[1]): p for p in args.clips.glob(f"turn_*_{args.run}_*.mp4")}
    narrated = sorted(s for s in spans if s in files)
    work = args.out / "snippets"
    work.mkdir(parents=True, exist_ok=True)
    for seg in narrated:
        first, last = spans[seg]
        mid = (first + last) / 2
        cut(files[seg], first, last, work / f"{seg}.wav")
        cut(files[seg], first, mid, work / f"{seg}a.wav")
        cut(files[seg], mid, last, work / f"{seg}b.wav")
    all_pairs = [(a, b) for i, a in enumerate(narrated) for b in narrated[i + 1:]]
    items = [{"kind": "cross", "a": a, "b": b} for a, b in rng.sample(all_pairs, PAIRS)]
    items += [{"kind": "control", "a": f"{s}a", "b": f"{s}b"} for s in rng.sample(narrated, CONTROLS)]
    rng.shuffle(items)
    blind = args.out / "listen"
    blind.mkdir(parents=True, exist_ok=True)
    key = []
    for n, item in enumerate(items, start=1):
        name = f"pair_{n:02d}.wav"
        join(work / f"{item['a']}.wav", work / f"{item['b']}.wav", blind / name)
        key.append({"file": name, **item})
    (args.out / "answer_key.json").write_text(json.dumps({"seed": args.seed, "clips": narrated, "items": key}, indent=1))
    with (blind / "responses.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "same voice? (yes/no)", "confidence 1-3", "note"])
        for k in key:
            w.writerow([k["file"], "", "", ""])
    (blind / "README.txt").write_text(
        "Each file is two short stretches of a film's narrator, 0.6 s apart.\n"
        "For each file, answer in responses.csv: is it the same voice speaking both times? (yes/no),\n"
        "and how sure you are (1 = guessing, 3 = certain). Listen once or twice, on headphones, in order.\n"
        "Do not open answer_key.json (one folder up) until everyone has answered.\n")
    print(f"{len(items)} items ({PAIRS} cross-clip, {CONTROLS} controls) from {len(narrated)} narrated clips → {blind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
