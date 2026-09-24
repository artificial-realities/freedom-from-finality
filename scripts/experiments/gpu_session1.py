"""GPU session 1 of the decision record (§2, Q8): X-03, X-04, X-05, X-06 and
LTX-09's rerun check on one warmed LTX-2.5 server.

    prepare   the prompt chains of every arm, from session A's replay file, locally
    run       render the arms against a server, clip by clip, resumable
    analyse   render ratios, seam jumps, figure boxes and colours, loudness,
              rerun equality; contact sheets for the by-eye checks

    python scripts/experiments/gpu_session1.py prepare --out logs/experiments/gpu1
    python scripts/experiments/gpu_session1.py run --server http://localhost:8000 --out logs/experiments/gpu1 [--only x05,x06]
    python scripts/experiments/gpu_session1.py analyse --out logs/experiments/gpu1

Arms (every chain starts from the story's reference image, as a run does;
seeds are derived from a key per seed index and the segment, so the arms
of an experiment share their noise, LTX-09):

    x05   session A segments 1-12, the production compiler vs the prose
          compiler (LTX-01), seeds 0, 1, 2
    x06   the same chain on the prose compiler with the four anchor arms of
          scripts/experiments/x06_forms.json (none, style, identity, both)
    x04   segments 17-28 (the viewer's "start breakdancing" lands on the
          fourth clip, segment 20), continuity windows 1, 9, 17
    x03   segments 1-30, 145 frames (today) vs 153 (144 new + the 9-frame
          overlap kept in the file, LTX-12's shape)
    ltx09 x05's production chain, seed 0, rendered again: reruns must
          reproduce
    x03av six clips of each shape, rendered after a server restart with
          LTX2_AUDIO_CEILING_LUFS set (AUD-03's candidate pads the audio to
          the video): is picture and sound the same length

Every request, response header and clip is kept under <out>/<exp>/<arm>/.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import requests  # noqa: E402

from src.i2v.ltxv import derive_seed, fit_reference  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.story import compiler as labels_compiler  # noqa: E402
from src.story import compiler_prose  # noqa: E402

REPLAY = REPO / "logs" / "batch-005" / "session-a.replay.json"
STORY = REPO / "stories" / "lego-void"
FORMS = REPO / "scripts" / "experiments" / "x06_forms.json"
FPS = 24.0
TIMEOUT_S = 900


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


replay_compiler = _module("replay_compiler", REPO / "scripts" / "replay_compiler.py")


# ── Prepare ──────────────────────────────────────────────────────────

def _chain(compiler, story: Story, first: int, count: int) -> list[dict]:
    data = json.loads(REPLAY.read_text())
    config = Config.from_yaml(REPO / "configs" / "director.live.yaml")
    rows = replay_compiler.replay(data, compiler=compiler, config=config, story=story)
    return [{"segment": r["segment"], "prompt": r["prompt"], "words": r["words"]}
            for r in rows if first <= r["segment"] < first + count and r.get("prompt")]


def _x06_story(arm: dict, forms: dict) -> Story:
    story = Story.from_json(STORY)
    hero = story.characters[0].model_copy(update={"look_short": arm["look_short"]})
    return story.model_copy(update={"visual_style": forms["visual_style"], "visual_anchor": arm["visual_anchor"],
                                    "characters": [hero, *story.characters[1:]]})


def _no_fallback_prose(beat, index, world, *, visual_style, visual_anchor="", **kw):
    """The prose compiler with an empty anchor meaning no anchor on a
    continuing clip (not the style's first sentence): X-06's none and
    identity arms."""
    if not kw.get("opening") and not visual_anchor:
        visual_style = ""
    return compiler_prose.compile_clip(beat, index, world, visual_style=visual_style, visual_anchor=visual_anchor, **kw)


def plan_arms() -> dict[str, dict[str, dict]]:
    """Every experiment's arms: the chain, the render settings, the seed key."""
    story = Story.from_json(STORY)
    labels, prose = labels_compiler.compile_clip, compiler_prose.compile_clip
    forms = json.loads(FORMS.read_text())
    base = {"frames": 145, "window": 9}
    exps: dict[str, dict[str, dict]] = {"x05": {}, "x06": {}, "x04": {}, "x03": {}, "ltx09": {}, "x03av": {}}
    for seed in (0, 1, 2):
        exps["x05"][f"labels-s{seed}"] = {**base, "chain": _chain(labels, story, 1, 12), "key": f"x05-seed{seed}"}
        exps["x05"][f"prose-s{seed}"] = {**base, "chain": _chain(prose, story, 1, 12), "key": f"x05-seed{seed}"}
    for name, arm in forms["arms"].items():
        exps["x06"][name] = {**base, "chain": _chain(_no_fallback_prose, _x06_story(arm, forms), 1, 12), "key": "x06-seed0"}
    for window in (1, 9, 17):
        exps["x04"][f"window-{window}"] = {"frames": 145, "window": window,
                                           "chain": _chain(labels, story, 17, 12), "key": "x04-seed0"}
    for frames in (145, 153):
        exps["x03"][f"frames-{frames}"] = {"frames": frames, "window": 9,
                                           "chain": _chain(labels, story, 1, 30), "key": "x03-seed0"}
    exps["ltx09"]["rerun"] = {**base, "chain": _chain(labels, story, 1, 12), "key": "x05-seed0"}
    for frames in (145, 153):
        exps["x03av"][f"frames-{frames}"] = {"frames": frames, "window": 9,
                                             "chain": _chain(labels, story, 1, 6), "key": "x03-seed0"}
    return exps


def prepare(out: Path) -> dict:
    exps = plan_arms()
    for exp, arms in exps.items():
        for arm, spec in arms.items():
            folder = out / exp / arm
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "arm.json").write_text(json.dumps(spec, indent=1))
    clips = sum(len(a["chain"]) for arms in exps.values() for a in arms.values())
    summary = {exp: {arm: {"clips": len(a["chain"]), "frames": a["frames"], "window": a["window"], "key": a["key"],
                           "words_mean": round(statistics.mean(c["words"] for c in a["chain"]), 1)}
                     for arm, a in arms.items()} for exp, arms in exps.items()}
    (out / "plan.json").write_text(json.dumps({"clips": clips, "arms": summary}, indent=1))
    print(f"{clips} clips in {sum(len(a) for a in exps.values())} arms → {out}")
    for exp, arms in summary.items():
        for arm, s in arms.items():
            print(f"  {exp:6} {arm:14} {s['clips']:3} clips, {s['frames']} frames, window {s['window']}, "
                  f"{s['words_mean']} words mean, seeds '{s['key']}'")
    return summary


# ── Run ──────────────────────────────────────────────────────────────

def _reference(server: str) -> str:
    health = requests.get(f"{server}/health", timeout=10).json()
    story = Story.from_json(STORY)
    png, notes = fit_reference(story.seed_image, int(health["width"]), int(health["height"]))
    return base64.b64encode(png).decode()


def render_arm(server: str, folder: Path, reference: str) -> None:
    spec = json.loads((folder / "arm.json").read_text())
    done = folder / "renders.jsonl"
    rendered = {json.loads(x)["segment"] for x in done.read_text().splitlines()} if done.exists() else set()
    if len(rendered) == len(spec["chain"]):
        return
    if rendered:
        # A chain continues from the server's window; a partial arm starts over.
        done.unlink()
        for p in folder.glob("clip_*.mp4"):
            p.unlink()
    requests.post(f"{server}/reset", timeout=30)
    for n, clip in enumerate(spec["chain"]):
        payload = {"prompt": clip["prompt"], "num_frames": spec["frames"], "continuity_strength": 1.0,
                   "continuity_frames": spec["window"], "seed": derive_seed(spec["key"], clip["segment"]),
                   "reset_window": n == 0, "reference_image": reference if n == 0 else None}
        t0 = time.monotonic()
        resp = requests.post(f"{server}/generate", json=payload, timeout=TIMEOUT_S)
        wall = time.monotonic() - t0
        if resp.status_code != 200:
            raise RuntimeError(f"{folder.name} segment {clip['segment']}: {resp.status_code} {resp.text[:300]}")
        path = folder / f"clip_{n + 1:02d}_seg{clip['segment']:03d}.mp4"
        path.write_bytes(resp.content)
        row = {"segment": clip["segment"], "index": n + 1, "path": path.name, "wall_s": round(wall, 3),
               "seed": payload["seed"], "frames": spec["frames"], "window": spec["window"],
               "sha256": hashlib.sha256(resp.content).hexdigest(),
               # uvicorn sends header names in lower case: keep them lower case.
               "headers": {k.lower(): v for k, v in resp.headers.items() if k.lower().startswith("x-")}}
        with done.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"  {folder.parent.name}/{folder.name} {n + 1:2}/{len(spec['chain'])} seg {clip['segment']:3} "
              f"{wall:5.2f} s  {row['headers'].get('x-timings', '')[:60]}", flush=True)


def run(server: str, out: Path, only: list[str] | None) -> None:
    health = requests.get(f"{server}/health", timeout=10).json()
    (out / "health.json").write_text(json.dumps(health, indent=1))
    # On the GPU box the tree arrives by git archive, without .git: the shipper passes the revision.
    head = os.environ.get("AR_REVISION") or subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                                           capture_output=True, text=True).stdout.strip()
    dirty = os.environ.get("AR_DIRTY", "") == "1" or bool(subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture_output=True, text=True).stdout.strip())
    with (out / "runs.jsonl").open("a") as f:
        f.write(json.dumps({"started": time.time(), "revision": head, "dirty": dirty, "only": only,
                            "health": health}) + "\n")
    print(f"server: {health.get('gpu')} {health.get('variant')} {health.get('quant')} compiled={health.get('compiled')} "
          f"{health.get('width')}x{health.get('height')} warm {health.get('warm_frames')}")
    reference = _reference(server)
    order = ["x05", "ltx09", "x06", "x04", "x03", "x03av"]
    for exp in order:
        if (only and exp not in only) or (not only and exp == "x03av") or not (out / exp).is_dir():
            continue      # x03av only on request, after the restart with the ceiling
        for folder in sorted((out / exp).iterdir()):
            if (folder / "arm.json").exists():
                t0 = time.monotonic()
                render_arm(server, folder, reference)
                print(f"{exp}/{folder.name}: {time.monotonic() - t0:.0f} s", flush=True)


# ── Analyse ──────────────────────────────────────────────────────────

def _frames(path: Path, indices: list[int] | None = None, size=(160, 88)):
    import numpy as np
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
    return frames if indices is None else frames[[min(i, len(frames) - 1) for i in indices]]


def _figure(frame) -> dict:
    """The non-white region of a frame: its box (fractions of the frame) and
    the shares of the minifigure's colours inside it (yellow, red, blue)."""
    import numpy as np
    f = frame.astype(np.int16)
    mask = f.min(axis=2) < 190
    if mask.sum() < 20:
        return {"area": 0.0}
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    px = f[mask]
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    yellow = ((r > 150) & (g > 120) & (b < 90)).mean()
    red = ((r > 140) & (g < 90) & (b < 90)).mean()
    blue = ((b > 110) & (r < 90)).mean()
    return {"area": round(mask.mean(), 4), "cx": round(xs.mean() / w, 3), "cy": round(ys.mean() / h, 3),
            "top": round(ys.min() / h, 3), "bottom": round(ys.max() / h, 3),
            "yellow": round(float(yellow), 3), "red": round(float(red), 3), "blue": round(float(blue), 3)}


def _seams(paths: list[Path], hidden: int, window: int = 9) -> list[dict]:
    """Per seam, over N's median frame-to-frame change:

    - `jump`: the last shown frame of N (the file's last, or `hidden`
      frames before it) against the first of N+1: a player that plays
      each file whole, as today's does.
    - `join`: the overlap player's cut (LTX-12): N's frame n − w − 1, the
      last it shows, against N+1's frame 0, which repeats N's frame n − w.
      One frame of motion when the window reproduces the tail.
    - `overlap_error_mean`: how closely N+1's first w frames repeat N's
      last w (the tail the window conditions on)."""
    import numpy as np
    clips = [_frames(p).astype(np.float32) for p in paths]
    out = []
    for n, (a, b) in enumerate(zip(clips, clips[1:])):
        last = a[len(a) - 1 - hidden]
        jump = float(np.abs(last - b[0]).mean())
        motion = float(np.median(np.abs(np.diff(a, axis=0)).mean(axis=(1, 2, 3))))
        w = min(window, len(a) - 1, len(b))
        join = float(np.abs(a[len(a) - w - 1] - b[0]).mean())
        # N's own frame steps just before the cut: a join no larger than these is not a spike.
        steps = np.abs(np.diff(a[max(0, len(a) - w - 5):len(a) - w], axis=0)).mean(axis=(1, 2, 3))
        local = float(np.median(steps)) if len(steps) else motion
        overlap = [float(np.abs(b[k] - a[len(a) - w + k]).mean()) for k in range(w)]
        out.append({"seam": n + 1, "jump": round(jump, 2), "motion": round(motion, 2),
                    "jump_over_motion": round(jump / motion, 2) if motion else None,
                    "join": round(join, 2), "join_over_motion": round(join / motion, 2) if motion else None,
                    "local_motion": round(local, 2), "join_over_local": round(join / local, 2) if local else None,
                    "overlap_error_mean": round(statistics.mean(overlap), 2)})
    return out


def _sheet(paths: list[Path], dst: Path, indices=(0, 72, 144)) -> None:
    """A contact sheet: one row per clip, the frames at `indices`."""
    import numpy as np
    from PIL import Image
    rows = [np.concatenate(list(_frames(p, list(indices), size=(256, 141))), axis=1) for p in paths]
    Image.fromarray(np.concatenate(rows, axis=0)).save(dst)


def analyse(out: Path) -> dict:
    ceiling = _module("audio_ceiling", REPO / "server" / "audio_ceiling.py")
    report: dict = {}
    for exp_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        for folder in sorted(p for p in exp_dir.iterdir() if (p / "renders.jsonl").exists()):
            spec = json.loads((folder / "arm.json").read_text())
            rows = [json.loads(x) for x in (folder / "renders.jsonl").read_text().splitlines()]
            paths = [folder / r["path"] for r in rows]
            delivered = (spec["frames"] - (spec["window"] if spec["frames"] > 145 else 0)) / FPS
            ratios = [r["wall_s"] / delivered for r in rows]
            warm = ratios[3:] or ratios
            # The server's own generate + encode, without the HTTP transfer.
            server = [(float(r["headers"].get("x-latency-s", 0)) + float(r["headers"].get("x-encode-s", 0))) / delivered
                      for r in rows]
            server_warm = server[3:] or server
            figure = []
            for p in paths:
                fr = _frames(p, [0, 72, 144])
                figure.append([_figure(f) for f in fr])
            ref = figure[0][0]
            drift = [max(abs(f.get(k, 0) - ref.get(k, 0)) for f in clip for k in ("cx", "cy", "top", "bottom"))
                     for clip in figure]
            colour = [max(abs(f.get(k, 0) - ref.get(k, 0)) for f in clip for k in ("yellow", "red", "blue"))
                      for clip in figure]
            loud = [ceiling.measure(str(p)) for p in paths]
            av = []
            for p in paths:
                info = json.loads(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(p)],
                    capture_output=True, text=True).stdout or "{}")
                d = {s["codec_type"]: float(s["duration"]) for s in info.get("streams", []) if s.get("duration")}
                av.append(round((d.get("audio", 0) - d.get("video", 0)) * 1000, 1) if "audio" in d else None)
            entry = {
                "clips": len(rows), "frames": spec["frames"], "window": spec["window"],
                "words_mean": round(statistics.mean(c["words"] for c in spec["chain"]), 1),
                "ratio_p50": round(statistics.median(warm), 3),
                "ratio_p95": round(sorted(warm)[min(len(warm) - 1, int(0.95 * len(warm)))], 3),
                "ratio_max": round(max(warm), 3),
                "server_ratio_p50": round(statistics.median(server_warm), 3),
                "server_ratio_p95": round(sorted(server_warm)[min(len(server_warm) - 1, int(0.95 * len(server_warm)))], 3),
                "server_ratio_max": round(max(server_warm), 3),
                "box_drift_max": [round(d, 3) for d in drift], "colour_drift_max": [round(c, 3) for c in colour],
                "lufs": [round(v, 1) if v is not None else None for v, _ in loud],
                "true_peak": [round(v, 1) if v is not None else None for _, v in loud],
                "audio_minus_video_ms": av,
                "seams": _seams(paths, hidden=spec["window"] if spec["frames"] > 145 else 0, window=spec["window"]),
                "sha256": [r["sha256"] for r in rows],
            }
            _sheet(paths, folder / "sheet.png")
            report[f"{exp_dir.name}/{folder.name}"] = entry
            print(f"{exp_dir.name}/{folder.name:14} ratio p50 {entry['ratio_p50']} p95 {entry['ratio_p95']} "
                  f"max {entry['ratio_max']}; box drift max {max(drift):.3f}; colour drift max {max(colour):.3f}; "
                  f"seam jump/motion median {statistics.median(s['jump_over_motion'] or 0 for s in entry['seams']):.2f}, "
                  f"join/local median {statistics.median(s['join_over_local'] or 0 for s in entry['seams']):.2f} "
                  f"max {max(s['join_over_local'] or 0 for s in entry['seams']):.2f}")
    rerun, first = report.get("ltx09/rerun"), report.get("x05/labels-s0")
    if rerun and first:
        import numpy as np
        same = sum(a == b for a, b in zip(rerun["sha256"], first["sha256"]))
        a_dir, b_dir = out / "ltx09" / "rerun", out / "x05" / "labels-s0"
        diffs = []
        for a, b in zip(sorted(a_dir.glob("clip_*.mp4")), sorted(b_dir.glob("clip_*.mp4"))):
            fa, fb = _frames(a).astype(np.float32), _frames(b).astype(np.float32)
            n = min(len(fa), len(fb))
            diffs.append(round(float(np.abs(fa[:n] - fb[:n]).mean()), 3))
        other = report.get("x05/labels-s1")
        report["ltx09"] = {"byte_identical": f"{same} of {len(rerun['sha256'])}", "mean_abs_diff_per_clip": diffs,
                           "note": "0-255 pixel scale; a different seed's chain for scale: x05/labels-s1"}
        if other:
            sdir = out / "x05" / "labels-s1"
            scale = []
            for a, b in zip(sorted(b_dir.glob("clip_*.mp4")), sorted(sdir.glob("clip_*.mp4"))):
                fa, fb = _frames(a).astype(np.float32), _frames(b).astype(np.float32)
                n = min(len(fa), len(fb))
                scale.append(round(float(np.abs(fa[:n] - fb[:n]).mean()), 3))
            report["ltx09"]["other_seed_diff_per_clip"] = scale
        print(f"ltx09: {same} of {len(rerun['sha256'])} byte-identical; mean abs pixel diff per clip {diffs}")
    (out / "analysis.json").write_text(json.dumps(report, indent=1))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("prepare", "run", "analyse"))
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "experiments" / "gpu1")
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--only", default="", help="comma-separated experiments to run")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "prepare":
        prepare(args.out)
    elif args.command == "run":
        run(args.server, args.out, [x for x in args.only.split(",") if x] or None)
    else:
        analyse(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
