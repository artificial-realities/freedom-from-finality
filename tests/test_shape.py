"""One duration authority (RUN-01) and the client/server warm-shape
contract (RUN-08, CLEAN-05): a clip's length is its selected shape in
frames, every consumer reads it, and a run whose server has not warmed a
registered shape is refused before anything renders."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from src.i2v.shape import ClipShape, ShapeRegistry, UnregisteredShape, main as shape_main, seconds_label
from src.models.config import Config
from src.models.story import Story


def _config(**over) -> Config:
    base = dict(name="shapetest", graph="director", llm_backend="mock", model="mock", buffer_seconds=0.0)
    base.update(over)
    return Config(**base)


def test_a_clip_shape_is_frames():
    six = ClipShape.from_seconds(6, fps=24, overlap_frames=9)
    assert (six.render_frames, six.delivered_frames, six.overlap_frames) == (145, 145, 9)
    assert six.delivered_seconds == 145 / 24 and seconds_label(six.delivered_seconds) == "6"
    assert ClipShape.from_seconds(5).render_frames == 121 and ClipShape.from_seconds(1).render_frames == 25
    assert seconds_label(ClipShape.from_seconds(4.5).delivered_seconds) == "4.7", "113 frames"
    try:
        six.render_frames = 1
        raise AssertionError("a shape is immutable")
    except Exception as exc:
        assert "frozen" in str(exc).lower()


def test_the_registry_holds_the_run_s_shapes():
    """The config's clip_seconds is the default; extra shapes are registered
    for experiments and never selected; unknown frame counts are refused;
    the warm check names what is missing and how to warm it."""
    reg = ShapeRegistry.from_config(_config(clip_seconds=6, extra_clip_seconds=[4, 8]), fps=24)
    assert reg.frames == [97, 145, 193] and reg.select() is reg.default and reg.default.render_frames == 145
    assert reg.get(97).delivered_seconds == 97 / 24
    for bad in (121, 144):
        try:
            reg.get(bad)
            raise AssertionError(f"{bad} is not registered")
        except UnregisteredShape:
            pass
    reg.check_warm([145, 97, 193, 121])
    try:
        reg.check_warm([145])
        raise AssertionError("two shapes are not warm")
    except UnregisteredShape as exc:
        assert "[97, 193]" in str(exc) and "LTX2_WARMUP_FRAMES=97,97,97,145,145,145,193,193,193" in str(exc)
    try:
        reg.check_warm(None)
        raise AssertionError("a server that does not report warm_frames promises nothing")
    except UnregisteredShape:
        pass


def test_one_duration_field():
    """`i2v_duration` is gone: an old config's value maps once onto
    clip_seconds (where it applied when clip_seconds was 0); no shipped
    config sets it; the warm-up list is printed from the client's registry."""
    assert "i2v_duration" not in Config.model_fields
    assert _config(i2v_duration=5).clip_seconds == 5 and _config(i2v_duration=5, clip_seconds=0).clip_seconds == 5
    assert _config(i2v_duration=5, clip_seconds=6).clip_seconds == 6 and _config().clip_seconds == 6
    for cfg in ("configs/director.yaml", "configs/director.live.yaml", "configs/director.replay.yaml"):
        assert "i2v_duration" not in Path(cfg).read_text(), cfg
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert shape_main(["configs/director.live.yaml"]) == 0
    assert out.getvalue().strip() == "153,153,153", "the live config's overlap shape (D-03), warmed three times"
    vast = Path("server/vast_run.sh").read_text()
    assert "LTX2_WARMUP_FRAMES=${LTX2_WARMUP_FRAMES:-$(warm_frames)}" in vast and "src.i2v.shape" in vast


class _Health:
    """requests.get/post stand-ins answering /health with `warm` frames."""

    def __init__(self, warm, fps=24, up=True):
        self.warm, self.fps, self.up, self.posts = warm, fps, up, []

    def __enter__(self):
        from src.i2v import ltxv

        class _Resp:
            def __init__(self, js=None, body=b""):
                self.status_code, self._js, self.content, self.headers, self.text = 200, js or {}, body, {}, ""

            def json(self):
                return self._js

        def get(url, timeout=0):
            if not self.up:
                raise ConnectionError("down")
            js = {"status": "ready", "frame_rate": self.fps, "width": 640, "height": 352}
            if self.warm is not None:
                js["warm_frames"] = self.warm
            return _Resp(js=js)

        def post(url, json=None, timeout=0):
            self.posts.append(json)
            return _Resp(body=b"\x00" * 64)

        self._orig = ltxv.requests.get, ltxv.requests.post
        ltxv.requests.get, ltxv.requests.post = get, post
        return self

    def __exit__(self, *exc):
        from src.i2v import ltxv
        ltxv.requests.get, ltxv.requests.post = self._orig


def test_the_client_checks_the_server_s_warm_shapes():
    """RUN-08 with mocked /health: one shape, several shapes, a missing
    shape, no warm list, another frame rate, an unreachable server."""
    from src.i2v.ltxv import LtxvI2V
    one = ShapeRegistry.from_config(_config(), fps=24)
    many = ShapeRegistry.from_config(_config(extra_clip_seconds=[4, 8]), fps=24)
    with tempfile.TemporaryDirectory() as d:
        client = LtxvI2V(server_url="http://x", output_dir=d)
        with _Health([145]):
            assert client.check_shapes(one) == {"frames": [145], "warm_frames": [145]}
        with _Health([97, 145, 193]):
            assert client.check_shapes(many)["frames"] == [97, 145, 193]
        for health, words in ((_Health([145]), "[97, 193]"), (_Health(None), "warm_frames"),
                              (_Health([145], fps=25), "25 fps"), (_Health([145], up=False), "not reachable")):
            with health:
                try:
                    LtxvI2V(server_url="http://x", output_dir=d).check_shapes(many if words == "[97, 193]" else one)
                    raise AssertionError(words)
                except UnregisteredShape as exc:
                    assert words in str(exc), (words, str(exc))
        with _Health([145]) as http:
            client._synthesize_sync("stories/lego-void/legoman-16x9.png", "p", 1, one.default)
        assert http.posts[0]["num_frames"] == 145, "the request carries the shape's frames"


async def test_a_run_is_refused_when_its_shape_is_not_warm():
    """The live loop checks the shapes before anything renders; a refusal
    ends the run there with the verdict in the timing log."""
    from src.eval.runner import run_live
    from tests.test_engine import Scripted
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d, _Health([121, 169]) as http:
        config = _config(video_enabled=True, i2v_backend="ltxv", ltxv_server_url="http://x")
        try:
            await run_live(config=config, story=story, log_dir=d, player=Scripted(), max_segments=2)
            raise AssertionError("the run must be refused")
        except UnregisteredShape as exc:
            assert "[145]" in str(exc)
        log = next((Path(d) / "sessions").iterdir()) / "timing.jsonl"
        records = [json.loads(x) for x in log.read_text().splitlines()]
    refused = [r for r in records if r["event"] == "shapes"]
    assert refused and "[145]" in refused[0]["refused"]
    assert not http.posts, "nothing rendered"


async def test_every_consumer_reads_the_selected_shape():
    """RUN-01 through the live loop with replayed clips: the Director is
    told the shape's length, the render request carries its frames, the
    player holds each clip for its delivered seconds, the event record and
    the story clock count them; a clip file of another length is flagged."""
    from src.eval import runner
    from src.i2v.replay import ReplayI2V
    from src.llm.mock_backend import MockBackend
    from tests.test_engine import Scripted
    story = Story.from_json("stories/lego-void")
    shapes_seen, systems = [], []
    synthesize, generate = ReplayI2V.synthesize, MockBackend.generate_structured

    async def spy_render(backend, *, shape, **kw):
        shapes_seen.append(shape)
        return await synthesize(backend, shape=shape, **kw)

    async def spy_llm(backend, messages, schema, *a, **kw):
        if schema.__name__ == "BeatPlan":
            systems.append(messages[0]["content"])
        return await generate(backend, messages, schema, *a, **kw)

    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x32:r=24",
                        "-frames:v", "25", "-pix_fmt", "yuv420p", str(clips / "a.mp4")], check=True)
        config = _config(video_enabled=True, clip_seconds=1, i2v_backend="replay", i2v_replay_dir=str(clips))
        player = Scripted()
        held: list[float] = []
        play = player.play

        async def spy_play(item):
            held.append(item.duration)
            await play(item)
        player.play = spy_play
        with mock.patch.object(ReplayI2V, "synthesize", spy_render), \
             mock.patch.object(MockBackend, "generate_structured", spy_llm):
            state = await runner.run_live(config=config, story=story, log_dir=d, player=player, max_segments=3)
        world_dir = next((Path(d) / "sessions").iterdir())
        records = [json.loads(x) for x in (world_dir / "timing.jsonl").read_text().splitlines()]
        from src.story.world import WorldModel
        world = WorldModel.load(world_dir)

        wrong = _config(video_enabled=True, clip_seconds=2, i2v_backend="replay", i2v_replay_dir=str(clips))
        await runner.run_live(config=wrong, story=story, log_dir=Path(d) / "wrong", player=Scripted(), max_segments=1)
        wrong_records = [json.loads(x) for x in next((Path(d) / "wrong" / "sessions").iterdir())
                         .joinpath("timing.jsonl").read_text().splitlines()]
    one = 25 / 24
    assert [s.render_frames for s in shapes_seen] == [25, 25, 25]
    assert held == [one] * 3 and [h.shot.duration_seconds for h in state.history] == [one] * 3
    assert [e.duration for e in world.events] == [one] * 3 and world.scene_seconds() == 3 * one
    assert all("clips of 1 seconds" in s for s in systems), "the Director is told the shape's length"
    assert not [r for r in records if r["event"] == "shape_mismatch"]
    mismatch = [r for r in wrong_records if r["event"] == "shape_mismatch"]
    assert mismatch and mismatch[0]["frames"] == 49 and abs(mismatch[0]["measured_s"] - one) < 0.01


def test_the_server_warms_neutrally_and_reports_its_shapes():
    """CLEAN-05: the warm-up sentence is story-agnostic; /health lists the
    warm frame counts; each turn logs its frames and prompt words and says
    when a frame count was not warmed. (Read from the source: the server
    needs a GPU to import.)"""
    source = Path("server/ltx2_server.py").read_text()
    start = source.index("WARMUP_PROMPT = (")
    prompt = source[start:source.index(")", start)]
    assert "LEGO" not in prompt and "minifigure" not in prompt.lower()
    assert '"warm_frames": sorted(_warm_frames)' in source and "_warm_frames.add(" in source
    assert "prompt words" in source and "NOT WARMED" in source
    subprocess.run(["python", "-m", "py_compile", "server/ltx2_server.py"], check=True)


def test_the_buffer_is_explicit_and_startup_is_startup():
    """RUN-03: `lead_clips` is `startup_clips` (the startup gate and queue
    size only) and the steady state is `buffer_seconds`; an old config maps
    onto both (its slack plus what the old formula kept at the baseline's
    render). The JIT simulation holds the schedule to the buffer
    (`tests/test_jit.py`)."""
    from src.models.config import LEGACY_SLACK_TO_BUFFER
    assert "lead_clips" not in Config.model_fields and "render_slack_seconds" not in Config.model_fields
    assert _config().startup_clips == 0 and Config(name="t", llm_backend="mock", model="m").buffer_seconds == 4.7
    old = Config(name="t", llm_backend="mock", model="m", lead_clips=1, render_slack_seconds=3.0)
    assert old.startup_clips == 1 and old.buffer_seconds == 3.0 + LEGACY_SLACK_TO_BUFFER == 4.7
    live = Config.from_yaml("configs/director.live.yaml")
    assert live.startup_clips == 1 and live.buffer_seconds == 4.7
    for cfg in ("configs/director.yaml", "configs/director.live.yaml", "configs/director.replay.yaml"):
        text = Path(cfg).read_text()
        assert "lead_clips" not in text and "render_slack_seconds" not in text, cfg


def test_the_director_is_told_lengths_in_seconds_and_how_the_renderer_reads():
    """ARCH-10: the beat guidance is time from the shape and the four-clip
    cap. LTX-04: three generic renderer rules and `camera_motion` as a verb
    phrase, in the (cached) system prompt."""
    from src.agents import director
    from src.llm.mock_backend import MockBackend
    from src.state.story_state import StoryState
    from src.story.world import WorldModel
    import asyncio
    story = Story.from_json("stories/lego-void")
    seen: list[str] = []

    class Capture(MockBackend):
        async def generate_structured(self, messages, schema, *a, **kw):
            seen.append(messages[0]["content"])
            return await super().generate_structured(messages, schema, *a, **kw)

    state = StoryState.initialize(story, config_name="t")
    state.turn_number = 1
    for seconds in (6, 4):
        shape = ClipShape.from_seconds(seconds)
        asyncio.run(director.run(world=WorldModel.from_story(story), state=state, llm=Capture(), config=_config(),
                                 voiceover_open=False, pacing_note="p", scene_note="s", beat_status="b",
                                 clip_seconds=shape.delivered_seconds))
    six, four = seen
    assert "one to 4 clips of 6 seconds each, in order: a beat is 6 to 24 seconds of film" in six
    assert "most beats run 12 to 18 seconds, a long held state may take all 24" in six
    assert "clips of 4 seconds each" in four and "a beat is 4 to 16 seconds" in four, "from the shape, not a constant"
    assert "One plausible movement per clip" in six and 'joined by "a moment later"' in six
    assert "No inner states without a visible cue" in six and "About 6 seconds of action per clip" in six
    assert '`camera_motion`: "static", or the move as a verb phrase without its subject' in six
