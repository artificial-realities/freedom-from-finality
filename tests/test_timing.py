"""Every run writes `logs/sessions/<id>/timing.jsonl`: what the live loop
lives by (fires, renders, queue, air time, viewer lines, Director calls)
and the browser's own view of each swap. Runs on the mock LLM; video
comes from the replay backend."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import aiohttp

from src.eval.runner import run_live
from src.i2v.ltxv import LtxvI2V, render_headers
from src.models.config import Config
from src.models.story import Story
from src.playback.player import Player
from src.util.timing_log import TimingLog
from src.web import WebPlayer

STORY = "stories/lego-void"
LINE = "A soccer ball appears at your feet."


def _config(**over) -> Config:
    base = dict(name="timingtest", graph="director", llm_backend="mock", model="mock",
                min_pause_seconds=6.0, render_slack_seconds=0.0)
    base.update(over)
    return Config(**base)


def _tiny_clip(path: Path, seconds: float = 1.0) -> Path:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi",
                    "-i", f"color=c=white:s=128x80:d={seconds}:r=24",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _records(log_dir: str) -> list[dict]:
    [path] = Path(log_dir).glob("sessions/*/timing.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _of(records: list[dict], event: str) -> list[dict]:
    return [r for r in records if r["event"] == event]


def _index(records: list[dict], event: str, turn: int) -> int:
    return next(i for i, r in enumerate(records) if r["event"] == event and r.get("turn") == turn)


class Scripted(Player):
    """Plays instantly and speaks a given line when a given clip starts."""

    def __init__(self, lines: dict[int, str] | None = None) -> None:
        super().__init__(time_scale=1000)
        self.lines = dict(lines or {})

    async def play(self, item) -> None:
        if item.turn in self.lines:
            self.push_input(self.lines.pop(item.turn))
        await self._hold(item.duration)


def test_timing_log_writes_json_lines_and_never_raises():
    with tempfile.TemporaryDirectory() as d:
        log = TimingLog(Path(d) / "a" / "b" / "timing.jsonl")
        log.write("first", n=1)
        log.write("second", text="ö", wall=3.0, t=-1)
        log.close()
        log.write("after close")  # ignored
        lines = (Path(d) / "a" / "b" / "timing.jsonl").read_text(encoding="utf-8").splitlines()
        first, second = (json.loads(x) for x in lines)
        assert first["event"] == "first" and first["n"] == 1 and first["t"] >= 0 and first["wall"] > 1e9
        assert second["text"] == "ö" and second["t"] >= first["t"]
        assert second["wall"] > 1e9 and second["t"] >= 0, "a field cannot overwrite the record's own"

        blocker = Path(d) / "file"
        blocker.write_text("")
        TimingLog(blocker / "timing.jsonl").write("x")  # parent is a file: no log, no exception
    TimingLog(None).write("x")


async def test_player_gives_each_line_an_id():
    class Quiet(Player):
        async def play(self, item) -> None:
            pass

    seen: list[tuple[int, str]] = []
    p = Quiet()
    p.on_line = lambda line_id, text: seen.append((line_id, text))
    p.push_input(" make it rain ")
    p.push_input("   ")
    p.push_input("and a dog appears")
    assert seen == [(1, "make it rain"), (3, "and a dog appears")], "blank lines are not reported"
    assert await p.next_input() == "make it rain and a dog appears"
    assert p.taken == [1, 3]
    assert await p.next_input() == "" and p.taken == []


async def test_live_text_run_writes_the_timing_log():
    story = Story.from_json(STORY)
    player = Scripted(lines={1: LINE})
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(), story=story, log_dir=d, player=player, max_segments=7)
        records = _records(d)
        session = next(Path(d).glob("sessions/*"))
        assert (session / "world.json").exists(), "the log sits next to the world model"

    assert [r["t"] for r in records] == sorted(r["t"] for r in records), "records are in time order"
    start, end = records[0], records[-1]
    assert start["event"] == "session_start" and start["loop"] == "live"
    assert start["config"] == "timingtest" and start["video"] is False and start["clip_seconds"] == 5
    assert start["session"] == session.name and "revision" in start and "dirty" in start
    assert end["event"] == "session_end" and end["played"] == 7

    for turn in range(1, 8):
        order = [_index(records, e, turn) for e in ("emit", "enqueued", "playing", "hold_end")]
        assert order == sorted(order), f"segment {turn}: emitted, queued, on air, off air"
    playing = _of(records, "playing")
    assert [p["first"] for p in playing] == [True] + [False] * 6
    assert all(p["hold"] >= 0 and p["waited"] >= 0 for p in playing)
    enqueued = _of(records, "enqueued")
    assert enqueued[0]["margin"] is None, "no margin while the lead builds"
    assert all(e["margin"] is not None for e in enqueued[1:])
    emits = _of(records, "emit")
    assert emits[0]["beat"] == 1 and emits[0]["clip"] == 1 and emits[0]["of"] == 2 and emits[0]["seconds"] == 5
    assert not _of(records, "fire"), "a text film renders nothing"

    starts, ends = _of(records, "director_start"), _of(records, "director_end")
    assert len(starts) == len(ends) >= 3
    assert [e["bootstrap"] for e in ends] == [True] + [False] * (len(ends) - 1)
    assert all(e["clips"] >= 1 and e["director_s"] <= e["plan_s"] and not e["fallback"] for e in ends)
    assert any(e["retry"] for e in ends), "the mock's rejected proposal shows as a re-ask"
    assert sum(e["input"] for e in ends) == 1, "one call answered the viewer"

    [received] = _of(records, "line_received")
    assert received["line"] == 1 and received["on_air"] == 1 and received["into"] >= 0
    assert received["chars"] == len(LINE) and len(received["sha"]) == 12
    [taken] = _of(records, "line_taken")
    [landed] = _of(records, "line_landed")
    assert taken["lines"] == landed["lines"] == [1]
    landed_turn = next(h.turn for h in state.history if h.user_input == LINE)
    assert landed["turn"] == landed_turn == 3
    answer = next(e for e in ends if e["input"])
    assert answer["turn"] == landed_turn
    assert not _of(records, "line_dropped")


async def test_live_video_run_records_fires_renders_and_the_queue():
    story = Story.from_json(STORY)
    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        _tiny_clip(clips / "a.mp4")
        config = _config(video_enabled=True, clip_seconds=1, i2v_backend="replay",
                         i2v_replay_dir=str(clips), i2v_replay_delay=0.05)
        await run_live(config=config, story=story, log_dir=d, player=Scripted(), max_segments=4)
        records = _records(d)

    assert records[0]["video"] is True and records[0]["i2v_backend"] == "replay"
    assert records[0]["clip_seconds"] == 1
    fires, done = _of(records, "fire"), _of(records, "render_done")
    assert [f["turn"] for f in fires] == [d_["turn"] for d_ in done] == [1, 2, 3, 4]
    assert fires[0]["due_in"] is None, "the first render fires while the lead builds"
    assert all(f["seconds"] == 1 for f in fires)
    assert all(r["ok"] and r["render_s"] >= 0.05 and r["server"]["source"] == "a.mp4" for r in done)
    assert all(r["wall"] > 1e9 for r in records), "every record keeps its Unix time"
    for turn in range(1, 5):
        order = [_index(records, e, turn) for e in ("emit", "fire", "render_done", "enqueued", "playing")]
        assert order == sorted(order), f"segment {turn}: emitted, fired, rendered, queued, on air"
    assert all(abs(e["duration"] - 1.0) < 0.2 for e in _of(records, "enqueued")), "probed clip length"


async def test_beacon_route_keeps_known_kinds_and_fields():
    player = WebPlayer(port=0)
    got: list[dict] = []
    player.on_beacon = got.append
    await player.start(title="t", session_id="s")
    try:
        async with aiohttp.ClientSession() as http:
            url = player.url + "beacon"
            body = {"kind": "swap", "turn": 3, "ms": 12.5, "prefetched": True, "armed": False,
                    "offset": 0.0, "extra": "dropped", "media_time": True}
            # sendBeacon posts a string: JSON as text/plain.
            async with http.post(url, data=json.dumps(body), headers={"Content-Type": "text/plain"}) as r:
                assert r.status == 204
            async with http.post(url, data=json.dumps({"kind": "waiting", "turn": 4, "media_time": 2.1})) as r:
                assert r.status == 204
            async with http.post(url, data=json.dumps({"kind": "reboot"})) as r:
                assert r.status == 400
            async with http.post(url, data="not json") as r:
                assert r.status == 400
            async with http.post(url, data=json.dumps({"kind": "swap", "pad": "x" * 4000})) as r:
                assert r.status == 413
    finally:
        await player.stop()
    assert got == [
        {"kind": "swap", "turn": 3, "ms": 12.5, "offset": 0.0, "armed": False, "prefetched": True},
        {"kind": "waiting", "turn": 4, "media_time": 2.1},
    ], "unknown fields and a boolean posing as a number are dropped"


async def test_beacons_from_the_page_land_in_the_timing_log():
    story = Story.from_json(STORY)
    player = WebPlayer(port=0, time_scale=20)
    with tempfile.TemporaryDirectory() as d:
        run = asyncio.create_task(run_live(config=_config(), story=story, log_dir=d,
                                           player=player, max_segments=4))
        for _ in range(200):
            if player._runner is not None:
                break
            await asyncio.sleep(0.05)
        async with aiohttp.ClientSession() as http:
            async with http.post(player.url + "beacon", data=json.dumps({"kind": "swap", "turn": 1, "ms": 8})) as r:
                assert r.status == 204
            async with http.post(player.url + "input", json={"text": LINE}) as r:
                assert r.status == 200
        await run
        records = _records(d)
    [beacon] = _of(records, "beacon")
    assert beacon["kind"] == "swap" and beacon["turn"] == 1 and beacon["ms"] == 8
    [received] = _of(records, "line_received")
    assert received["chars"] == len(LINE)


def test_ltxv_render_headers_are_typed_for_the_log():
    parsed = render_headers({"X-Latency-S": "4.21", "X-Encode-S": "0.6", "X-Num-Frames": "145",
                             "X-Frame-Rate": "24", "X-Timings": "text=0.1 dit=3.5", "X-Duration-S": "oops",
                             "X-Unrelated": "1"})
    assert parsed == {"latency_s": 4.21, "encode_s": 0.6, "num_frames": 145, "frame_rate": 24.0,
                      "timings": "text=0.1 dit=3.5", "duration_s": "oops"}
    with tempfile.TemporaryDirectory() as d:
        i2v = LtxvI2V(server_url="http://x", output_dir=d)
        assert i2v.last_latency_s is None and i2v.last_render == {}
        i2v.last_render = {"latency_s": 4.21}
        assert i2v.last_latency_s == 4.21
