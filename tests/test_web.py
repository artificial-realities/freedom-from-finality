"""The live loop through the web player: the film streams over HTTP, input
posted mid-stream reaches Now, clips are served to the page, and
the played clips are saved as one film joined the way the page joins them.
Runs on the mock LLM; video comes from a prepared clip file."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import aiohttp

from src.runner import run_live
from src.models.config import Config
from src.models.story import FrameZero
from src.playback.player import Playable, Player
from src.web import WebPlayer
from tests.test_engine import rendering, tiny_clip


def _config(**over) -> Config:
    base = dict(name="webtest", llm_backend="mock")
    base.update(over)
    return Config(**base)


async def _wait_up(player: WebPlayer) -> None:
    for _ in range(200):
        if player._runner is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("web player did not start")


async def _read_events(session: aiohttp.ClientSession, url: str, on_event=None) -> list[tuple[str, dict]]:
    """Consume the SSE stream until `ended`; `on_event(name, data)` runs
    while the server is still up."""
    events: list[tuple[str, dict]] = []
    name = ""
    async with session.get(url + "events") as resp:
        assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/event-stream")
        async for raw in resp.content:
            line = raw.decode().rstrip("\n")
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                events.append((name, data))
                if on_event is not None:
                    await on_event(name, data)
                if name == "ended":
                    break
    return events


async def test_web_player_text_mode_streams_and_takes_input():
    story = FrameZero.from_json("tests/fixtures/lego-void")
    player = WebPlayer(port=0, time_scale=50)
    line = "A soccer ball appears at your feet."
    with tempfile.TemporaryDirectory() as d:
        run = asyncio.create_task(run_live(
            config=_config(), story=story, log_dir=d, player=player, max_segments=5,
        ))
        await _wait_up(player)
        async with aiohttp.ClientSession() as http:
            async with http.get(player.url) as r:
                assert r.status == 200 and "Suggest something" in await r.text()
            async with http.post(player.url + "input", json={"text": line}) as r:
                assert r.status == 200 and (await r.json())["ok"]
            async with http.post(player.url + "input", json={"text": "   "}) as r:
                assert r.status == 400, "blank lines are rejected"
            events = await _read_events(http, player.url)
        state = await run

        hello = events[0]
        assert hello[0] == "hello" and hello[1]["now"], \
            "a late joiner is told what is on air"
        playing = [e for n, e in events if n == "playing"]
        assert len(state.history) == 5 and playing, "segments played after the client connected"
        assert all(e["url"] is None and e["narration"] for e in playing), "text-only film: no clips"
        assert all(e["started_at"] > 0 and e["duration"] >= 3 for e in playing)
        assert all("path" not in e for e in playing), "the page never sees a local path"
        assert events[-1][0] == "ended" and events[-1][1]["segments"] == 5
        transcript = next(Path(d).glob("*.md")).read_text()
        assert f'— "{line}"' in transcript, "the posted line reached Now"

        async with aiohttp.ClientSession() as http:
            try:
                async with http.post(player.url + "input", json={"text": "late"}) as r:
                    assert r.status in (409, 404)
            except aiohttp.ClientConnectorError:
                pass  # server already down: also fine


async def test_web_player_serves_rendered_clips():
    story = FrameZero.from_json("tests/fixtures/lego-void")
    with tempfile.TemporaryDirectory() as d, rendering(Path(d) / "a.mp4"):
        tiny_clip(Path(d) / "a.mp4")
        config = _config(video_enabled=True, clip_seconds=1)
        player = WebPlayer(port=0, time_scale=50)
        run = asyncio.create_task(run_live(
            config=config, story=story, log_dir=d, player=player, max_segments=3,
        ))
        await _wait_up(player)
        fetched: list[int] = []

        async def fetch_clip(name: str, data: dict) -> None:
            # Clip bytes are served from the run's video dir, with Range support,
            # while the film is on air.
            if name != "playing":
                return
            base = player.url.rstrip("/")
            async with http.get(base + data["url"]) as r:
                assert r.status == 200 and r.headers["Content-Type"] == "video/mp4"
                assert len(await r.read()) > 500
            async with http.get(base + data["url"], headers={"Range": "bytes=0-99"}) as r:
                assert r.status == 206 and len(await r.read()) == 100
            async with http.get(base + "/clips/999.mp4") as r:
                assert r.status == 404
            fetched.append(data["turn"])

        async with aiohttp.ClientSession() as http:
            events = await _read_events(http, player.url, fetch_clip)
        playing = [e for n, e in events if n == "playing"]
        assert playing and all(e["url"] and e["url"].endswith(".mp4") for e in playing)
        assert fetched == [e["turn"] for e in playing]
        state = await run
        assert len(state.history) == 3
        assert all(e["duration"] == 1.0 for e in playing), "held for the shape's delivered frames"
        assert all(e["overlap_seconds"] == 9 / 24 for e in playing)
        film = next((Path(d) / "video").glob("webtest_*.mp4"))
        frames = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
                                 "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(film)],
                                capture_output=True, text=True, check=True).stdout.strip()
        assert frames == str(33 + 24 + 24), "each head that repeats the clip before is shown once"


async def test_player_input_joins_lines_and_pipelined_items_keep_views():
    class Capture(Player):
        def __init__(self):
            super().__init__(time_scale=1000)
            self.items: list[Playable] = []
        async def play(self, item):
            self.items.append(item)
            await self._hold(item.duration)

    p = Capture()
    p.push_input("make it rain")
    p.push_input("  ")
    p.push_input("and a dog appears")
    assert await p.next_input() == "make it rain and a dog appears"
    assert await p.next_input() == ""

    story = FrameZero.from_json("tests/fixtures/lego-void")
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(), story=story, log_dir=d, player=p, max_segments=3)
    assert [i.turn for i in p.items] == [1, 2, 3]
    assert p.items[0].dialogue == ["Lego Man: Hello? Anyone?"] and p.items[1].dialogue == []
    assert p.items[0].speech and not p.items[1].speech, "a clip with lines brings its sound in at once"
    assert len(state.history) == 3


async def test_input_takes_json_lines_only_and_caps_their_length():
    """Another page in the viewer's browser can post a form or plain text
    without asking, but not JSON: /input takes only JSON, so it cannot type
    into the film. A line longer than the page allows is refused, since each
    costs a Now call."""
    from src.web.server import MAX_INPUT_CHARS
    player = WebPlayer(host="127.0.0.1", port=0)
    await player.start(session_id="s")
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(player.url + "input", data='{"text": "a dog appears"}',
                                 headers={"Content-Type": "text/plain"}) as r:
                assert r.status == 415
            async with http.post(player.url + "input", json={"text": "x" * (MAX_INPUT_CHARS + 1)}) as r:
                assert r.status == 413
            async with http.post(player.url + "input", json=["a dog appears"]) as r:
                assert r.status == 400
            async with http.post(player.url + "input", json={"text": "a dog appears"}) as r:
                assert r.status == 200
        assert await player.next_input() == "a dog appears"
    finally:
        await player.stop()
