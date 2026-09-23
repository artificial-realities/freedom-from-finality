"""Seam check: what a viewer sees at every clip-to-clip join, measured
against the seam gates in `benchmarks/runtime/baseline.json` that
`bench_report` leaves to this script. Run it after touching `player.html`,
`src/web/` or the overlap on the server (RUN-13).

Two parts; either runs alone.

  files    Duplicate frames per seam, from the clip files (ffmpeg + numpy).
           The last 16 frames of clip N and the first 16 of clip N+1 give an
           error matrix; the alignment score for an overlap of k frames is
           the mean error along the diagonal that maps N+1's first k frames
           onto N's last k. A valley at k is a k-frame overlap; a monotone
           rise from k = 1 is a continuation (0); a flat profile is
           undetermined: without motion the frames are the same either way
           and a viewer sees no hold. The method cannot resolve a single
           duplicated frame; the gate targets the nine-frame hold.
           `--hidden-frames` is what the player skips at a seam (0 today),
           so the displayed count is the file overlap minus that.
  browser  The real `WebPlayer` serves the same clips to Playwright's
           headless Chromium at the live schedule's three queue timings
           (review 03 §3): A, a mid-beat clip queued 1.3 s after the
           previous went on air; B, a beat's first clip queued 0.06 s after,
           inside the crossfade; C, two clips queued at once. In the page a
           requestAnimationFrame loop records each video element's computed
           opacity, turn, media time and readiness, and the luminance of
           the composite drawn on a small canvas the way the CSS composites
           it (a black stage, then each element with its opacity). The dip
           at a seam is 1 - min luminance within 0.3 s of the switch over
           the darker of the medians 0.3-1.0 s before and after. Swap
           latency and reloads on `playing` come from the page's own
           beacons through a TimingLog, read by `bench_report.measure`.

Playwright is not a project dependency: point `--browser-python` at an
interpreter that has it (on this WSL box, the memory note's setup:
`LD_LIBRARY_PATH=$PWD/logs/_scratch/libs/lib` and
`--browser-python logs/_scratch/pwenv/bin/python`). Without it, or with
`--no-browser`, only the file part runs.

    python scripts/seam_check.py logs/live-box/video --run 20260918 --clips 12 --no-browser
    LD_LIBRARY_PATH=$PWD/logs/_scratch/libs/lib python scripts/seam_check.py logs/live-box/video \\
        --run 20260918 --clips 12 --browser-python logs/_scratch/pwenv/bin/python

Results go to <out>/seam_check.json (default logs/seam_check/<label>/), with
the browser samples and the timing log alongside. Exit status is 1 when a
measured gate fails, as with `bench_report`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "benchmarks" / "runtime" / "baseline.json"

FRAME_W, FRAME_H = 160, 96      # analysis resolution for the file part
BLOCK = 8                       # block size of the frame error (the busiest block counts)
K_MAX = 15                      # largest overlap the profile considers
VALLEY_DEPTH = 0.2              # a valley must be this fraction of its own value below both neighbours

# The live schedule's queue timings (review 03 §3), as (clips before, seconds after that clip went on air).
QUEUE_AT = {"A": (1, 1.3), "B": (1, 0.06), "C": (2, 3.0)}

DIP_WINDOW_S = 0.3              # the switch's neighbourhood searched for the minimum
DIP_REF_S = (0.3, 1.0)          # the reference windows before and after the switch


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
    """Video duration, frame rate and frame count, and the audio duration."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,duration,nb_frames,r_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    info = {"video_s": None, "fps": None, "frames": None, "audio_s": None}
    for s in json.loads(out).get("streams", []):
        if s.get("codec_type") == "video":
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            info["fps"] = float(num) / float(den or 1)
            info["video_s"] = float(s["duration"]) if s.get("duration") else None
            info["frames"] = int(s["nb_frames"]) if s.get("nb_frames") else None
        elif s.get("codec_type") == "audio":
            info["audio_s"] = float(s["duration"]) if s.get("duration") else None
    return info


def clip_frames(path: Path, width: int = FRAME_W, height: int = FRAME_H):
    """Every frame of the clip as grey uint8 (n, height, width)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={width}:{height},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width)


# ── Duplicate frames ─────────────────────────────────────────────────

def block_error(a, b, block: int = BLOCK) -> float:
    """The busiest block's mean absolute difference between two grey frames
    (the 99th percentile over blocks): motion is local, decoder noise is
    everywhere, so this sees a moving figure that a whole-frame mean hides."""
    import numpy as np
    d = np.abs(a.astype(np.float32) - b.astype(np.float32))
    h, w = d.shape[0] // block * block, d.shape[1] // block * block
    d = d[:h, :w].reshape(h // block, block, w // block, block).mean(axis=(1, 3))
    return float(np.percentile(d, 99))


def overlap_profile(tail, head, k_max: int = K_MAX) -> list[float]:
    """Alignment score for each overlap k = 1..k_max: the mean block error
    between head[j] and tail[-k + j] over j < k. `tail` is the end of one
    clip, `head` the start of the next."""
    import numpy as np
    K = min(len(tail), len(head), k_max + 1)
    tail, head = tail[-K:], head[:K]
    E = np.array([[block_error(tail[i], head[j]) for j in range(K)] for i in range(K)])
    return [float(np.mean([E[K - k + j, j] for j in range(k)])) for k in range(1, K)]


def estimate_overlap(profile: list[float], *, depth: float = VALLEY_DEPTH) -> tuple[int | None, str]:
    """(frames, note): the overlap the profile shows, or None when the seam
    has too little motion to tell. A valley at k >= 2 is an overlap of k; a
    profile lowest at k = 1 and rising from there is a continuation, the
    error growing with the shift; anything else is undetermined."""
    if len(profile) < 3:
        return None, "profile too short"
    best = min(range(len(profile)), key=profile.__getitem__)
    k = best + 1
    floor = profile[best]
    if 2 <= k < len(profile):
        left, right = profile[best - 1], profile[best + 1]
        if min(left, right) - floor >= depth * max(floor, 1e-6):
            return k, "valley"
    if k == 1 and profile[1] >= profile[0] and profile[2] - profile[0] >= depth * max(profile[0], 1e-6):
        return 0, "continuation"
    return None, "undetermined (no motion across the seam)"


def seams_from_files(clips: list[tuple[int, Path]], *, hidden_frames: int = 0) -> dict:
    """Per-seam overlap estimates and their summary for consecutive clips."""
    frames = {turn: clip_frames(path) for turn, path in clips}
    rows = []
    for (a, _), (b, _) in zip(clips, clips[1:]):
        profile = overlap_profile(frames[a][-(K_MAX + 1):], frames[b][: K_MAX + 1])
        overlap, note = estimate_overlap(profile)
        rows.append({
            "from": a, "to": b, "overlap": overlap,
            "displayed": None if overlap is None else max(0, overlap - hidden_frames),
            "note": note, "profile": [round(x, 3) for x in profile],
        })
    determined = [r for r in rows if r["overlap"] is not None]
    displayed = sorted(r["displayed"] for r in determined)
    histogram: dict[str, int] = {}
    for r in determined:
        histogram[str(r["overlap"])] = histogram.get(str(r["overlap"]), 0) + 1
    return {
        "seams": len(rows), "determined": len(determined), "undetermined": len(rows) - len(determined),
        "hidden_frames": hidden_frames, "overlap_histogram": dict(sorted(histogram.items(), key=lambda kv: int(kv[0]))),
        "duplicate_frames_per_seam": _median(displayed),
        "duplicate_frames_max": max(displayed) if displayed else None,
        "rows": rows,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    n = len(xs)
    return float(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


# ── Browser: the page side (needs only Playwright) ───────────────────

SAMPLER_JS = """
window.__samples = [];
document.addEventListener('DOMContentLoaded', () => {
  const W = 64, H = 36;
  const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const videos = [...document.querySelectorAll('.stage video')];
  const step = () => {
    const t = (performance.timeOrigin + performance.now()) / 1000;
    ctx.globalAlpha = 1; ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
    const state = videos.map(v => {
      const op = +getComputedStyle(v).opacity;
      if (op > 0 && v.readyState >= 2) { ctx.globalAlpha = op; try { ctx.drawImage(v, 0, 0, W, H); } catch (e) {} }
      return [op, v.dataset.turn ? +v.dataset.turn : 0, +v.currentTime.toFixed(3), v.readyState,
              v.classList.contains('active') ? 1 : 0];
    });
    const d = ctx.getImageData(0, 0, W, H).data; let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    window.__samples.push([t, sum / (d.length / 4), state]);
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
});
"""


async def browser_page(url: str, out: Path, seconds: float) -> None:
    """Open the player, click Watch, sample the page for `seconds`, write
    the samples. Runs under the interpreter that has Playwright."""
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1100, "height": 620})
        await page.add_init_script(SAMPLER_JS)
        await page.goto(url)
        await page.wait_for_timeout(500)
        try:
            await page.click("#watch", timeout=3000)
        except Exception:
            pass
        await page.wait_for_timeout(int(seconds * 1000))
        samples = await page.evaluate("() => window.__samples")
        await browser.close()
    out.write_text(json.dumps({"url": url, "browser": browser.version, "samples": samples}))


# ── Browser: the driver side (the real WebPlayer) ───────────────────

def case_of(turn: int, cases: str) -> str:
    """The queue-timing case of the clip `turn` (the first clip has none;
    the second is always A, there being no clip two before it)."""
    if turn <= 2:
        return "A"
    return cases[(turn - 3) % len(cases)]


async def run_browser(clips: list[tuple[int, Path]], *, out: Path, browser_python: str, cases: str = "ABC",
                      page_delay: float = 2.0, seam: str = "fade", overlap_frames: int = 0) -> dict:
    """`seam="overlap"` runs the page's X-02 candidate: each clip but the
    first opens on `overlap_frames` that repeat the previous clip's tail,
    and every clip is held for its length minus the next clip's overlap
    (the page cuts at the overlap and crossfades the sound)."""
    from src.playback.player import Playable
    from src.util.timing_log import TimingLog
    from src.web import WebPlayer

    out.mkdir(parents=True, exist_ok=True)
    items = []
    for n, (turn, path) in enumerate(clips):
        info = probe(path)
        fps = info.get("fps") or 24.0
        overlap = overlap_frames / fps if seam == "overlap" else 0.0
        last = n == len(clips) - 1
        items.append(Playable(turn=turn, path=str(path), duration=(info["video_s"] or 6.0) - (0 if last else overlap),
                              overlap_seconds=overlap if n else 0.0))
    timing = TimingLog(out / "timing.jsonl")
    player = WebPlayer(host="127.0.0.1", port=0, seam=seam)
    player.on_beacon = lambda record: timing.write("beacon", **record)
    await player.start(title="seam check", session_id="seam-check")
    timing.write("session_start", session="seam-check", loop="seam_check", config="seam_check", video=True,
                 i2v_backend="files", clip_seconds=round(sum(i.duration for i in items) / len(items), 3))
    seconds = sum(i.duration for i in items) + page_delay + 5.0
    samples_path = out / "samples.json"
    proc = await asyncio.create_subprocess_exec(browser_python, __file__, "--browser-page", player.url,
                                                str(samples_path), str(seconds))
    for _ in range(300):
        if player._clients or proc.returncode is not None:
            break
        await asyncio.sleep(0.1)
    if proc.returncode is not None:
        await player.stop()
        raise RuntimeError(f"the browser process ended before connecting (exit {proc.returncode}); "
                           f"is Playwright installed for {browser_python}?")
    await asyncio.sleep(page_delay)

    by_turn = {i.turn: i for i in items}
    plan = {i.turn: case_of(i.turn, cases) for i in items}
    marks = []
    pending: list[asyncio.Task] = []

    async def later(delay: float, item: Playable) -> None:
        await asyncio.sleep(delay)
        player.prepare(item)
        marks.append({"turn": item.turn, "queued_at": time.time()})

    player.prepare(items[0])
    await asyncio.sleep(0.5)
    for n, item in enumerate(items):
        marks.append({"turn": item.turn, "playing_at": time.time(), "case": plan[item.turn]})
        timing.write("playing", turn=item.turn, duration=item.duration, hold=item.duration, waited=0.0, first=n == 0)
        play = asyncio.create_task(player.play(item))
        for later_turn in (item.turn + 1, item.turn + 2):
            if later_turn in by_turn:
                before, delay = QUEUE_AT[plan[later_turn]]
                if later_turn - item.turn == before:
                    pending.append(asyncio.create_task(later(delay, by_turn[later_turn])))
        await play
        timing.write("hold_end", turn=item.turn)
    for task in pending:
        if not task.done():
            task.cancel()
    await asyncio.sleep(1.0)
    timing.write("session_end", turn=items[-1].turn)
    timing.close()
    await player.stop()
    try:
        await asyncio.wait_for(proc.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        proc.kill()
    (out / "marks.json").write_text(json.dumps({"marks": marks, "plan": plan}, indent=1))
    if not samples_path.exists():
        raise RuntimeError("the browser wrote no samples")
    return {"samples": json.loads(samples_path.read_text()), "plan": plan,
            "records": [json.loads(line) for line in (out / "timing.jsonl").read_text().splitlines() if line.strip()]}


# ── Browser: the measurement ─────────────────────────────────────────

def _active(state: list) -> tuple[int, int]:
    """(element index, turn) of the element marked active, or (-1, 0)."""
    for index, (_op, turn, _ct, _rs, active) in enumerate(state):
        if active:
            return index, turn
    return -1, 0


def dips_from_samples(samples: list, *, window: float = DIP_WINDOW_S, ref: tuple[float, float] = DIP_REF_S) -> dict:
    """Per-switch luminance dips from the page's samples `[t, luminance,
    state]`; a switch is the first sample whose active element carries the
    next turn. Also notes an outgoing element reloaded with another clip
    while still visible (a prefetch inside the crossfade, review 03 §3 2d):
    its readiness drops to 0 or its turn changes at an opacity above 0.05."""
    ts = [s[0] for s in samples]
    lums = [s[1] for s in samples]
    rows = []
    previous, out_index = 0, -1
    for i, s in enumerate(samples):
        index, turn = _active(s[2])
        if turn and turn != previous:
            if previous:
                t_s = s[0]
                near = [lums[j] for j in range(len(samples)) if abs(ts[j] - t_s) <= window]
                before = [lums[j] for j in range(len(samples)) if -ref[1] <= ts[j] - t_s <= -ref[0]]
                after = [lums[j] for j in range(len(samples)) if ref[0] <= ts[j] - t_s <= ref[1]]
                if len(before) >= 5 and len(after) >= 5 and near:
                    reference = min(_median(before), _median(after))
                    lowest = min(near)
                    dip = 0.0 if reference <= 0 else max(0.0, 1.0 - lowest / reference)
                    at = min((j for j in range(len(samples)) if abs(ts[j] - t_s) <= window), key=lambda j: lums[j])
                    replaced = out_index >= 0 and any(
                        samples[j][2][out_index][0] > 0.05
                        and (samples[j][2][out_index][3] == 0 or samples[j][2][out_index][1] != previous)
                        for j in range(len(samples)) if 0 <= ts[j] - t_s <= window and out_index < len(samples[j][2])
                    )
                    rows.append({"from": previous, "to": turn, "dip": round(dip, 4), "reference": round(reference, 1),
                                 "min": round(lowest, 1), "min_at_ms": round((ts[at] - t_s) * 1000, 1),
                                 "samples_near": len(near), "replaced_during_fade": replaced})
                else:
                    rows.append({"from": previous, "to": turn, "dip": None, "note": "too few samples around the switch"})
            previous, out_index = turn, index
    measured = [r["dip"] for r in rows if r["dip"] is not None]
    dts = [b - a for a, b in zip(ts, ts[1:])]
    return {
        "switches": len(rows), "measured": len(measured),
        "crossfade_dip": _median(measured), "crossfade_dip_max": max(measured) if measured else None,
        "replaced_during_fade": sum(1 for r in rows if r.get("replaced_during_fade")),
        "sample_interval_ms": round(_median(dts) * 1000, 1) if dts else None,
        "rows": rows,
    }


# ── Gates ────────────────────────────────────────────────────────────

def gate_rows(files: dict | None, browser: dict | None, seams: dict | None, gates: dict) -> list[dict]:
    """PASS / FAIL / not measured for the four seam gates this script owns."""
    def row(name: str, value, limit: float, *, unit: str = "", lower_is_better: bool = True) -> dict:
        if value is None:
            return {"gate": name, "value": None, "threshold": limit, "result": "not measured"}
        ok = value <= limit if lower_is_better else value >= limit
        return {"gate": name, "value": value, "threshold": limit, "unit": unit, "result": "PASS" if ok else "FAIL"}
    rows = []
    if "duplicate_frames_per_seam" in gates:
        rows.append(row("duplicate_frames_per_seam", None if files is None else files["duplicate_frames_per_seam"],
                        gates["duplicate_frames_per_seam"]["max"], unit="frames"))
    if "crossfade_dip" in gates:
        rows.append(row("crossfade_dip", None if browser is None else browser["crossfade_dip"],
                        gates["crossfade_dip"]["max"]))
    if "swap_p95_ms" in gates:
        rows.append(row("swap_p95_ms", None if seams is None else seams["swap_ms"].get("p95"),
                        gates["swap_p95_ms"]["max"], unit="ms"))
    if "reloads_on_playing" in gates:
        rows.append(row("reloads_on_playing", None if seams is None else seams["reloads_on_playing"],
                        gates["reloads_on_playing"]["max"]))
    return rows


def _load_bench():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_report", REPO / "scripts" / "bench_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Main ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", nargs="?", type=Path, help="directory of a run's clips (turn_NNNN_<run>_*.mp4)")
    ap.add_argument("--run", help="the run date in the file names, when the directory holds several")
    ap.add_argument("--clips", dest="count", type=int, default=12, help="how many consecutive clips (default 12)")
    ap.add_argument("--first", type=int, default=None, help="start at this turn (default: the run's first)")
    ap.add_argument("--hidden-frames", type=int, default=0, help="frames the player skips at a seam (0 today)")
    ap.add_argument("--no-browser", action="store_true", help="only the file part")
    ap.add_argument("--browser-python", default=sys.executable, help="an interpreter with Playwright installed")
    ap.add_argument("--cases", default="ABC", help="queue-timing cases in rotation from the third clip (A, B, C)")
    ap.add_argument("--seam", choices=("fade", "overlap"), default="fade",
                    help="the page's seam: today's fade, or X-02's overlap candidate")
    ap.add_argument("--overlap-frames", type=int, default=9, help="with --seam overlap: the frames each clip repeats")
    ap.add_argument("--label", default=time.strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--out", type=Path, default=None, help="default logs/seam_check/<label>")
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--browser-page", nargs=3, metavar=("URL", "OUT", "SECONDS"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.browser_page:
        url, out, seconds = args.browser_page
        asyncio.run(browser_page(url, Path(out), float(seconds)))
        return 0
    if args.clips is None:
        ap.error("a clips directory is required")
    if any(c not in QUEUE_AT for c in args.cases) or not args.cases:
        ap.error(f"--cases takes letters from {''.join(QUEUE_AT)}")

    clips = find_clips(args.clips, args.run)
    if args.first is not None:
        clips = [c for c in clips if c[0] >= args.first]
    clips = clips[: args.count]
    if len(clips) < 2:
        print(f"need at least two clips; found {len(clips)} in {args.clips}", file=sys.stderr)
        return 2
    for (a, _), (b, _) in zip(clips, clips[1:]):
        if b != a + 1:
            print(f"clips are not consecutive: turn {a} is followed by {b}", file=sys.stderr)
            return 2
    out = args.out or (REPO / "logs" / "seam_check" / args.label)
    out.mkdir(parents=True, exist_ok=True)
    gates = json.loads(Path(args.baseline).read_text(encoding="utf-8"))["gates"]

    result: dict = {"label": args.label, "clips": [str(p) for _, p in clips], "turns": [t for t, _ in clips],
                    "hidden_frames": args.hidden_frames, "cases": args.cases, "seam": args.seam,
                    "overlap_frames": args.overlap_frames if args.seam == "overlap" else 0}
    result["files"] = seams_from_files(clips, hidden_frames=args.hidden_frames)

    browser = seams = None
    if not args.no_browser:
        run = asyncio.run(run_browser(clips, out=out, browser_python=args.browser_python, cases=args.cases,
                                      seam=args.seam, overlap_frames=args.overlap_frames))
        browser = dips_from_samples(run["samples"]["samples"])
        for row in browser["rows"]:
            row["case"] = run["plan"].get(row["to"])
            row["next_queued"] = run["plan"].get(row["to"] + 1)
        seams = _load_bench().measure(run["records"])["seams"]
        result["browser"] = browser
        result["beacons"] = seams
        result["browser_version"] = run["samples"].get("browser")
    result["gates"] = gate_rows(result["files"], browser, seams, gates)
    (out / "seam_check.json").write_text(json.dumps(result, indent=1))

    if args.json:
        print(json.dumps(result, indent=1))
    else:
        f = result["files"]
        print(f"Seam check {args.label}: clips {clips[0][0]}-{clips[-1][0]} from {args.clips}")
        print(f"  files: {f['determined']} of {f['seams']} seams determined, overlap histogram {f['overlap_histogram']}, "
              f"hidden {f['hidden_frames']} → duplicate frames per seam {f['duplicate_frames_per_seam']} "
              f"(max {f['duplicate_frames_max']})")
        if browser is not None:
            print(f"  browser ({result.get('browser_version')}): {browser['measured']} of {browser['switches']} switches measured "
                  f"at {browser['sample_interval_ms']} ms per sample; dip median {browser['crossfade_dip']} "
                  f"max {browser['crossfade_dip_max']}; outgoing element replaced during the fade: {browser['replaced_during_fade']}")
            for row in browser["rows"]:
                if row.get("dip") is not None:
                    print(f"    {row['from']}→{row['to']} case {row['case']}, next queued {row['next_queued'] or '-'}: "
                          f"dip {row['dip']:.1%} (min {row['min']} of {row['reference']} at {row['min_at_ms']:+.0f} ms)"
                          + ("  outgoing element replaced during the fade" if row["replaced_during_fade"] else ""))
            print(f"  beacons: {seams['swaps']} swaps, steady-state {seams['swap_ms']}, "
                  f"reloads on playing {seams['reloads_on_playing']}, waiting {seams['waiting']}")
        for row in result["gates"]:
            value = "—" if row["value"] is None else (f"{row['value']:.3g}" if isinstance(row["value"], float) else row["value"])
            print(f"  {row['result']:<12} {row['gate']:<28} {value} (≤ {row['threshold']})")
        print(f"  written to {out}")
    return 1 if any(r["result"] == "FAIL" for r in result["gates"]) else 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    sys.exit(main())
