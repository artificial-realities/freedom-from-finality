"""`scripts/bench_report.py` reads a run's timing log against the gates in
`benchmarks/runtime/baseline.json`, and the README carries the baseline
table it generates."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
from pathlib import Path

from src.eval.runner import run_live
from src.models.config import Config
from src.models.story import Story
from src.playback.player import Player

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("bench_report", REPO / "scripts" / "bench_report.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

BASELINE = json.loads((REPO / "benchmarks" / "runtime" / "baseline.json").read_text(encoding="utf-8"))
GATES = BASELINE["gates"]


def _film(*, clips: int = 12, render: float = 4.8, length: float = 6.0417, video: bool = True,
          slow: dict[int, float] | None = None, waits: dict[int, float] | None = None,
          margins: dict[int, float] | None = None, lines: list[tuple[int, float, int | None]] = (),
          dropped: list[int] = (), swaps: dict[int, float] | None = None, reloads: list[int] = ()) -> list[dict]:
    """A synthetic timing log: `clips` clips of `length` s, each rendered in
    `render` s (or `slow[turn]`), put on air back to back after a waited
    `waits[turn]`; `lines` are (line id, received t, landed turn or None)."""
    slow, waits, margins, swaps = slow or {}, waits or {}, margins or {}, swaps or {}
    recs: list[dict] = [{"t": 0.0, "event": "session_start", "session": "synthetic", "loop": "live",
                         "config": "synthetic", "video": video, "i2v_backend": "ltxv" if video else None,
                         "clip_seconds": 6}]
    t = 10.0
    for turn in range(1, clips + 1):
        recs.append({"t": t - 9, "event": "director_end", "turn": turn, "input": False, "plan_s": 3.0,
                     "beat": turn, "clips": 1, "director_s": 3.0, "retry": False, "fallback": False,
                     "bootstrap": turn == 1})
        if video:
            recs.append({"t": t - 8, "event": "fire", "turn": turn, "seconds": 6, "due_in": 8.0})
            recs.append({"t": t - 3, "event": "render_done", "turn": turn, "ok": True,
                         "render_s": slow.get(turn, render), "server": {"duration_s": round(length, 3)}})
        recs.append({"t": t - 3, "event": "enqueued", "turn": turn, "duration": length, "queued": 1,
                     "margin": margins.get(turn, None if turn == 1 else 4.7)})
        t += waits.get(turn, 0.0)
        recs.append({"t": t, "event": "playing", "turn": turn, "duration": length, "hold": length,
                     "waited": waits.get(turn, 0.0), "first": turn == 1})
        if turn in swaps:
            recs.append({"t": t + 0.01, "event": "beacon", "kind": "swap", "turn": turn, "ms": swaps[turn],
                         "prefetched": True, "armed": True})
        if turn in reloads:
            recs.append({"t": t + 0.01, "event": "beacon", "kind": "loadstart_on_playing", "turn": turn})
        t += length
        recs.append({"t": t, "event": "hold_end", "turn": turn})
    for line, received, turn in lines:
        recs.append({"t": received, "event": "line_received", "line": line, "chars": 10, "sha": "x"})
        if turn is not None:
            recs.append({"t": received + 1, "event": "line_landed", "turn": turn, "lines": [line]})
    for line in dropped:
        recs.append({"t": 1.0, "event": "line_received", "line": line, "chars": 5, "sha": "y"})
        recs.append({"t": 2.0, "event": "line_dropped", "turn": 2, "lines": [line], "reason": "cut"})
    recs.append({"t": t + 1, "event": "session_end", "played": clips})
    return sorted(recs, key=lambda r: r["t"])


def _rows(records: list[dict]) -> dict[str, dict]:
    m = bench.measure(records, warm_after=3, stall_s=GATES["stalls"]["threshold_s"],
                      compile_factor=GATES["mid_film_compiles"]["factor"])
    return {row["gate"]: row for row in bench.evaluate(m, GATES)}


def _main(records: list[dict] | None, *args: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "timing.jsonl"
        if records is not None:
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = bench.main([d, *args])
    return code, out.getvalue()


def test_statistics():
    assert bench.percentile([], 50) is None
    assert bench.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert abs(bench.percentile([float(x) for x in range(1, 101)], 95) - 95.05) < 1e-9
    assert bench.summary([]) == {"n": 0}
    s = bench.summary([2.0, 4.0])
    assert s["n"] == 2 and s["mean"] == 3.0 and s["sd"] == 1.0 and s["max"] == 4.0
    assert bench.slope([5.0]) is None and bench.slope([1.0, 3.0, 5.0]) == 2.0


def test_a_clean_video_run_passes_every_gate_it_can_measure():
    lines = [(1, 50.0, 8)]
    rows = _rows(_film(lines=lines, swaps={t: 2.0 for t in range(2, 13)}))
    assert {r["result"] for r in rows.values()} <= {"PASS", "n/a", "not measured here"}
    assert rows["render_ratio_p95"]["result"] == "PASS" and abs(rows["render_ratio_p95"]["value"] - 4.8 / 6.0417) < 1e-6
    assert rows["render_slope_ms_per_clip"]["result"] == "n/a", "the trend needs 150 warm clips"
    assert rows["stalls"]["value"] == 0 and rows["min_queue_margin_s"]["value"] == 4.7
    assert rows["first_picture_s"]["value"] == 10.0
    assert rows["swap_p95_ms"]["result"] == "PASS" and rows["reloads_on_playing"]["value"] == 0
    assert rows["hold_minus_media_ms"]["result"] == "PASS" and rows["hold_minus_media_ms"]["value"] < 1
    assert rows["duplicate_frames_per_seam"]["result"] == "not measured here"
    assert rows["crossfade_dip"]["result"] == "not measured here"
    code, out = _main(_film(lines=lines))
    assert code == 0 and "No gate failed" in out


def test_stalls_slow_renders_compiles_and_late_lines_fail():
    records = _film(clips=12, slow={6: 5.9, 9: 30.0}, waits={7: 0.4}, margins={7: -0.4},
                    lines=[(1, 10.0, 5)], swaps={5: 80.0, 6: 2.0}, reloads=[8])
    rows = _rows(records)
    assert rows["mid_film_compiles"]["result"] == "FAIL" and rows["mid_film_compiles"]["note"] == "turn 9"
    assert rows["render_ratio_max"]["result"] == "FAIL", "the compile is also the slowest render"
    assert rows["stalls"]["result"] == "FAIL" and rows["stalls"]["note"] == "turn 7; 0.40 s held"
    assert rows["min_queue_margin_s"]["result"] == "FAIL" and rows["min_queue_margin_s"]["value"] == -0.4
    assert rows["steering_p50_s"]["result"] == "FAIL", "received at 10 s, its clip on air 24 s later"
    assert rows["swap_p95_ms"]["result"] == "FAIL" and rows["reloads_on_playing"]["result"] == "FAIL"
    code, out = _main(records, "--json")
    report = json.loads(out)
    assert code == 1
    assert {"mid_film_compiles", "stalls", "min_queue_margin_s", "steering_p50_s"} <= set(report["failed"])


def test_warm_up_clips_do_not_count():
    rows = _rows(_film(slow={1: 30.0, 2: 12.0, 3: 9.0}))
    assert rows["render_ratio_max"]["result"] == "PASS" and rows["mid_film_compiles"]["value"] == 0


def test_lines_are_followed_from_receipt_to_the_screen():
    m = bench.measure(_film(clips=10, lines=[(1, 15.0, 4), (2, 30.0, 6), (3, 55.0, None)], dropped=[4]))
    film = m["film"]
    # Turn k goes on air at 10 + (k - 1) × 6.0417.
    expected = [10 + 3 * 6.0417 - 15, 10 + 5 * 6.0417 - 30]
    assert len(film["steering_s"]) == 2 and all(abs(a - b) < 1e-9 for a, b in zip(film["steering_s"], expected))
    assert film["lines_received"] == 4 and film["lines_landed"] == 2
    assert film["lines_dropped"] == [4] and film["lines_unlanded"] == [3]


def test_a_text_film_leaves_the_render_gates_unmeasured():
    rows = _rows(_film(video=False))
    for gate in ("render_ratio_p50", "render_ratio_p95", "render_ratio_max", "mid_film_compiles",
                 "render_slope_ms_per_clip", "hold_minus_media_ms"):
        assert rows[gate]["result"] == "n/a", gate
    assert rows["stalls"]["result"] == "PASS"


def test_unreadable_input_exits_2():
    assert _main(None)[0] == 2, "no timing.jsonl"
    assert _main([{"t": 0, "event": "playing", "turn": 1}])[0] == 2, "no session_start"


async def test_reads_a_real_run_of_the_live_loop():
    class Scripted(Player):
        def __init__(self) -> None:
            super().__init__(time_scale=1000)

        async def play(self, item) -> None:
            if item.turn == 1:
                self.push_input("A soccer ball appears at your feet.")
            await self._hold(item.duration)

    config = Config(name="benchtest", graph="director", llm_backend="mock", model="mock",
                    buffer_seconds=0.0)
    with tempfile.TemporaryDirectory() as d:
        await run_live(config=config, story=Story.from_json("stories/lego-void"), log_dir=d,
                       player=Scripted(), max_segments=6)
        [session] = Path(d).glob("sessions/*")
        m = bench.measure(bench.load_records(session))
    assert m["identity"]["config"] == "benchtest" and m["identity"]["video"] is False
    assert m["film"]["clips"] == 6 and m["film"]["lines_landed"] == 1 and len(m["film"]["steering_s"]) == 1
    assert m["director"]["calls"] >= 3 and m["director"]["retries"] >= 1
    assert sum(m["director"]["clips_per_beat"].values()) == m["director"]["calls"]
    rows = {row["gate"]: row for row in bench.evaluate(m, GATES)}
    assert rows["render_ratio_p95"]["result"] == "n/a" and rows["first_picture_s"]["result"] == "PASS"


def test_the_baseline_names_every_gate_the_report_knows():
    rows = bench.evaluate(bench.measure(_film()), GATES)
    assert rows and all(r["note"] != "the report does not know this gate" for r in rows)
    for section in ("identity", "hardware", "recipe", "shape", "cold_warm", "renders", "director", "film", "seams"):
        assert section in BASELINE, section
    assert BASELINE["gates"]["render_ratio_p95"]["max"] == 0.85, "RUN-04: the p95 gate"


def test_readme_carries_the_generated_baseline_table():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert bench.readme_section(readme) == bench.readme_table(BASELINE), \
        "README is out of date: python scripts/bench_report.py --update-readme"
