"""`scripts/seam_check.py` and `scripts/audio_check.py` on synthetic material
with known answers (RUN-13, part c): frame arrays and ffmpeg-made clips
with a known overlap, a luminance series with a known dip, and tone,
silence and click clips for the played mix. The browser half of the seam
check needs Playwright and is not run here."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seam = _load("seam_check")
audio = _load("audio_check")


# ── Seam check: duplicate frames ─────────────────────────────────────

def _moving_frames(n: int, *, motion: int = 2, noise: float = 1.0, seed: int = 0) -> np.ndarray:
    """`n` grey frames of a bright block sliding `motion` px per frame over
    mid grey, with decoder-like noise."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n):
        f = np.full((96, 160), 128.0)
        x = 20 + motion * i
        f[30:60, x:x + 24] = 220.0
        f += rng.normal(0, noise, f.shape)
        frames.append(np.clip(f, 0, 255).astype(np.uint8))
    return np.stack(frames)


def test_overlap_estimator_reads_an_overlap_a_continuation_and_no_motion():
    # The next clip carries its own decoder noise (seed 1): duplicated frames are near, not identical.
    frames, again = _moving_frames(48), _moving_frames(48, seed=1)
    tail = frames[:16]                                   # a clip ending at frame 15
    assert seam.estimate_overlap(seam.overlap_profile(tail, again[7:23])) == (9, "valley")      # opens with 7..15 again
    assert seam.estimate_overlap(seam.overlap_profile(tail, again[13:29])) == (3, "valley")
    assert seam.estimate_overlap(seam.overlap_profile(tail, again[16:32])) == (0, "continuation")
    static, static_again = _moving_frames(48, motion=0), _moving_frames(48, motion=0, seed=1)
    for head in (static_again[7:23], static_again[16:32]):
        overlap, note = seam.estimate_overlap(seam.overlap_profile(static[:16], head))
        assert overlap is None and note.startswith("undetermined")


def _write_clip(frames: np.ndarray, out: Path) -> Path:
    """The grey frames as a 24 fps H.264 clip."""
    n, h, w = frames.shape
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", "24",
                    "-i", "-", "-pix_fmt", "yuv420p", str(out)], input=frames.tobytes(), check=True)
    return out


def test_overlap_from_clip_files_and_the_hidden_frames():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        frames, again = _moving_frames(96), _moving_frames(96, seed=1)
        a = _write_clip(frames[:48], d / "a.mp4")
        b_overlap = _write_clip(again[39:87], d / "b_overlap.mp4")     # opens with a's last nine frames
        b_cont = _write_clip(again[48:96], d / "b_cont.mp4")           # continues where a ends
        assert seam.probe(a)["frames"] == 48 and seam.clip_frames(a).shape == (48, 96, 160)

        r = seam.seams_from_files([(1, a), (2, b_overlap)])
        assert r["determined"] == 1 and r["duplicate_frames_per_seam"] == 9 and r["rows"][0]["note"] == "valley"
        hidden = seam.seams_from_files([(1, a), (2, b_overlap)], hidden_frames=9)
        assert hidden["duplicate_frames_per_seam"] == 0 and hidden["rows"][0]["overlap"] == 9
        cont = seam.seams_from_files([(1, a), (2, b_cont)])
        assert cont["duplicate_frames_per_seam"] == 0 and cont["rows"][0]["note"] == "continuation"


def test_find_clips_by_run_and_the_plain_fallback():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for name in ("turn_0002_20260918_b.mp4", "turn_0001_20260918_a.mp4", "turn_0001_20260917_z.mp4",
                     "full_session.mp4"):
            (d / name).write_bytes(b"")
        assert [t for t, _ in seam.find_clips(d, "20260918")] == [1, 2]
        assert seam.find_clips(d, "20260101") == []
        plain = d / "plain"
        plain.mkdir()
        for name in ("b.mp4", "a.mp4"):
            (plain / name).write_bytes(b"")
        assert [(t, p.name) for t, p in audio.find_clips(plain)] == [(1, "a.mp4"), (2, "b.mp4")]


# ── Seam check: the browser samples ──────────────────────────────────

def _samples(dip: float, *, replaced: bool = False) -> list:
    """60 Hz page samples: 1.5 s on clip 1, a switch, 1.5 s on clip 2, the
    picture dipping by `dip` over a 250 ms fade after the switch."""
    out = []
    for i in range(180):
        t = i / 60
        lum = 225.0
        on_two = t >= 1.5
        if on_two and t < 1.75 and dip:
            u = (t - 1.5) / 0.25
            lum = 225.0 * (1 - dip * 4 * u * (1 - u))
        one = [0 if on_two else 1, 1, min(t, 1.5), 4, 0 if on_two else 1]
        two = [1 if on_two else 0, 2, max(0.0, t - 1.5), 4, 1 if on_two else 0]
        if replaced and 1.5 <= t < 1.6:
            one = [0.8, 3, 0.0, 0, 0]                    # the outgoing element reloaded with clip 3 mid-fade
        out.append([1000.0 + t, lum, [one, two]])
    return out


def test_dips_from_samples():
    r = seam.dips_from_samples(_samples(0.25))
    assert r["switches"] == 1 and r["measured"] == 1
    assert abs(r["crossfade_dip"] - 0.25) < 0.02 and r["replaced_during_fade"] == 0
    assert 0 <= r["rows"][0]["min_at_ms"] <= 250
    assert seam.dips_from_samples(_samples(0.0))["crossfade_dip"] == 0.0
    assert seam.dips_from_samples(_samples(0.0, replaced=True))["replaced_during_fade"] == 1
    assert seam.dips_from_samples(_samples(0.25)[:100])["rows"][0]["dip"] is None   # too few samples after


def test_gate_rows():
    gates = json.loads((REPO / "benchmarks" / "runtime" / "baseline.json").read_text())["gates"]
    rows = {r["gate"]: r for r in seam.gate_rows({"duplicate_frames_per_seam": 9}, {"crossfade_dip": 0.02},
                                                 {"swap_ms": {"p95": 1.2}, "reloads_on_playing": 0}, gates)}
    assert rows["duplicate_frames_per_seam"]["result"] == "FAIL"
    assert rows["crossfade_dip"]["result"] == "PASS"
    assert rows["swap_p95_ms"]["result"] == "PASS" and rows["reloads_on_playing"]["result"] == "PASS"
    unmeasured = {r["gate"]: r for r in seam.gate_rows({"duplicate_frames_per_seam": None}, None, None, gates)}
    assert all(r["result"] == "not measured" for r in unmeasured.values())


# ── Audio check ──────────────────────────────────────────────────────

def _clip(path: Path, source: str, *, seconds: float = 1.0, volume_db: float | None = None) -> Path:
    """A one-second white clip carrying the lavfi audio `source`."""
    cmd = ["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", source, "-f", "lavfi",
           "-i", f"color=c=white:s=160x96:r=24:d={seconds}", "-map", "0:a", "-map", "1:v"]
    if volume_db is not None:
        cmd += ["-af", f"volume={volume_db}dB"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _three_clips(d: Path) -> list[tuple[int, Path]]:
    tone = _clip(d / "turn_0001_20260101_tone.mp4", "aevalsrc=0.1*sin(2*PI*1000*t):s=48000:d=1")   # -20 dBFS
    silence = _clip(d / "turn_0002_20260101_silence.mp4", "anullsrc=r=48000:cl=mono:d=1")
    loud = _clip(d / "turn_0003_20260101_loud.mp4", "aevalsrc=0.95*cos(2*PI*1000*t):s=48000:d=1")   # starts at 0.95
    return [(1, tone), (2, silence), (3, loud)]


def test_audio_levels_and_silence_edges():
    with tempfile.TemporaryDirectory() as d:
        (_, tone), (_, silence), (_, loud) = _three_clips(Path(d))
        lufs, peak = audio.ebur128(tone)
        assert -24.5 < lufs < -21.5 and -21 < peak < -19          # a -20 dBFS sine
        assert audio.ebur128(silence) == (-70.0, float("-inf"))
        lufs_loud, peak_loud = audio.ebur128(loud)
        assert lufs_loud > -18 and peak_loud > -1.0
        lead, trail = audio.silence_edges(audio.pcm(tone))
        assert lead < 0.05 and trail < 0.05
        lead, trail = audio.silence_edges(audio.pcm(silence))
        assert lead > 0.9 and trail > 0.9


def test_mix_policies_steps_clicks_and_holes():
    with tempfile.TemporaryDirectory() as d:
        clips = _three_clips(Path(d))
        tracks = [(audio.pcm(p), 1.0) for _, p in clips]

        y, transitions = audio.mix(tracks, policy="today")            # the player today: a 250 ms fade-in
        assert transitions == [(1.0, 1.25), (2.0, 2.25)]
        rows = audio.seam_metrics(y, transitions)
        assert rows[0]["step_db"] >= 40                               # tone to silence
        assert rows[1]["click_db"] < audio.CLICK_DBFS                 # faded in: no click
        assert -9 < rows[1]["after_db"] < -2                          # the loud clip after the fade (AAC lowers a near-full-scale tone a little)

        y0, t0 = audio.mix(tracks, policy="today", fade_in_s=0.0)      # a hard join
        rows0 = audio.seam_metrics(y0, t0)
        assert rows0[1]["click_db"] >= audio.CLICK_DBFS               # silence into a cosine starting at 0.95
        assert -9 < rows0[1]["after_db"] < -2

        yo, to = audio.mix(tracks, policy="overlap", overlap_s=0.375)
        assert to == [(0.625, 1.0), (1.25, 1.625)] and len(yo) < len(y)   # each clip starts one overlap before the previous ends
        assert audio.seam_metrics(yo, to)[1]["click_db"] < audio.CLICK_DBFS   # crossfaded: no click

        holes = audio.holes(y)
        assert holes and max(h["seconds"] for h in holes) >= 0.9      # the silent clip


def test_check_end_to_end_with_events():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        clips = _three_clips(d)
        events = d / "events.jsonl"
        events.write_text('{"segment": 1, "voiceover": "Our subject hums."}\n{"segment": 2, "voiceover": ""}\n')
        result = audio.check(clips, events=events, ceiling_lufs=-18.0)
        m = result["metrics"]
        assert m["clips"] == 3 and m["seams"] == 2 and m["policy"] == "today"
        assert m["narrated_clips"] == 1 and result["clips_detail"][0]["words"] == 3
        assert m["clips_above_ceiling"] == 1 and m["true_peak_overs"] == 1       # the loud clip
        assert m["onset_clicks"] == 0 and m["holes"] >= 1 and m["holes_per_minute"] > 0
        assert m["narrated_step_median_lu"] > 14                                  # tone (-23) to silence (-70)
        assert m["trailing_silence_clips"] == 1
        results = {r["criterion"]: r["result"] for r in result["criteria"]}
        assert results["onset_clicks"] == "PASS"
        assert results["holes_per_minute"] == "FAIL" and results["true_peak_overs"] == "FAIL"
        assert results["narrated_step_median_lu"] == "FAIL"
        no_events = audio.check(clips)
        assert no_events["metrics"]["narrated_step_median_lu"] is None
        assert {r["criterion"]: r["result"] for r in no_events["criteria"]}["narrated_step_median_lu"] == "not measured"
