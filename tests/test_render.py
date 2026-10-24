"""What the client asks of the renderer: one clip shape in frames that
every consumer reads and the server has warmed (a run is refused before
anything renders otherwise), and a reference image at the render aspect."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from PIL import Image

from src.i2v.shape import ClipShape, RendererNotReady, main as shape_main, seconds_label
from src.models.config import Config
from src.models.story import FrameZero


def _config(**over) -> Config:
    base = dict(name="shapetest", llm_backend="mock", buffer_seconds=0.0)
    base.update(over)
    return Config(**base)


def test_a_clip_shape_is_frames():
    """New picture on top of the 9 frames that repeat the previous clip's
    tail, on the 8k + 1 grid; the new frames are what the viewer is given."""
    six = ClipShape.for_seconds(6, fps=24)
    assert (six.render_frames, six.delivered_frames, six.overlap_frames) == (153, 144, 9)
    assert six.delivered_seconds == 6.0 and seconds_label(six.delivered_seconds) == "6"
    assert ClipShape.for_seconds(1).render_frames == 33 and ClipShape.for_seconds(1).delivered_seconds == 1.0
    try:
        six.render_frames = 1
        raise AssertionError("a shape is immutable")
    except Exception as exc:
        assert "frozen" in str(exc).lower()


def test_the_server_must_have_warmed_the_shape():
    """The warm check names what is missing and how to warm it; the warm-up
    list `vast_run.sh` passes the server comes from the client."""
    shape = ClipShape.from_config(_config(clip_seconds=6), fps=24)
    shape.check_warm([153, 121])
    for warm, words in (([121], "LTX2_WARMUP_FRAMES=153,153,153"), (None, "warm_frames")):
        try:
            shape.check_warm(warm)
            raise AssertionError(words)
        except RendererNotReady as exc:
            assert words in str(exc), str(exc)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert shape_main(["configs/video.yaml"]) == 0
    assert out.getvalue().strip() == "153,153,153", "the video config's shape, warmed three times"
    vast = Path("server/vast_run.sh").read_text()
    assert "LTX2_WARMUP_FRAMES=$(python3 -W ignore::RuntimeWarning -m src.i2v.shape" in vast


class _Health:
    """requests.get/post stand-ins answering /health with `warm` frames;
    `status` plays a server still warming."""

    def __init__(self, warm, fps=24, up=True, status="ready"):
        self.warm, self.fps, self.up, self.posts = warm, fps, up, []
        self.status = status

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
            js = {"status": self.status, "frame_rate": self.fps, "width": 512, "height": 288}
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


def test_the_client_checks_the_server_s_warm_shape():
    """With a mocked /health: a warm shape passes and the render request
    carries its frames; a missing shape, no warm list, another frame rate,
    a server still warming and an unreachable one are refused."""
    from src.i2v.ltxv import LtxvI2V
    shape = ClipShape.from_config(_config(), fps=24)
    with tempfile.TemporaryDirectory() as d:
        client = LtxvI2V(server_url="http://x", output_dir=d)
        with _Health([153]):
            assert client.check_shape(shape)["warm_frames"] == [153]
        for health, words in ((_Health([121]), "[153]"), (_Health(None), "warm_frames"),
                              (_Health([153], fps=25), "25 fps"), (_Health([153], up=False), "not reachable"),
                              (_Health([], status="warming"), "still warming")):
            with health:
                try:
                    LtxvI2V(server_url="http://x", output_dir=d).check_shape(shape)
                    raise AssertionError(words)
                except RendererNotReady as exc:
                    assert words in str(exc), (words, str(exc))
        with _Health([153]) as http:
            client._synthesize_sync("tests/fixtures/lego-void/legoman-16x9.png", "p", 1, shape)
        assert http.posts[0]["num_frames"] == 153, "the request carries the shape's frames"
        assert "reference_image" in http.posts[0], "the first render seeds from the reference"


async def test_a_run_is_refused_when_its_shape_is_not_warm():
    """The live loop checks the shape before anything renders; a refusal
    ends the run there with the verdict in the timing log."""
    from src.runner import run_live
    from tests.test_engine import Scripted
    story = FrameZero.from_json("tests/fixtures/lego-void")
    with tempfile.TemporaryDirectory() as d, _Health([121, 169]) as http:
        config = _config(video_enabled=True, ltxv_server_url="http://x")
        try:
            await run_live(config=config, story=story, log_dir=d, player=Scripted(), max_segments=2)
            raise AssertionError("the run must be refused")
        except RendererNotReady as exc:
            assert "[153]" in str(exc)
        log = next((Path(d) / "sessions").iterdir()) / "timing.jsonl"
        records = [json.loads(x) for x in log.read_text().splitlines()]
    refused = [r for r in records if r["event"] == "shapes"]
    assert refused and "[153]" in refused[0]["refused"]
    assert not http.posts, "nothing rendered"


async def test_every_consumer_reads_the_selected_shape():
    """Through the live loop with rendered clip files: Now is told the
    shape's length, the render request carries its frames, the player
    holds each clip for its delivered seconds, the event record counts
    them; a clip file of another length is flagged."""
    from src import runner
    from src.llm.mock_backend import MockBackend
    from tests.test_engine import Clips, Scripted, rendering, tiny_clip
    story = FrameZero.from_json("tests/fixtures/lego-void")
    shapes_seen, systems = [], []
    synthesize, generate = Clips.synthesize, MockBackend.generate_structured

    async def spy_render(backend, *, shape, **kw):
        shapes_seen.append(shape)
        return await synthesize(backend, shape=shape, **kw)

    async def spy_llm(backend, messages, schema, *a, **kw):
        if schema.__name__ == "BeatPlan":
            systems.append(messages[0]["content"])
        return await generate(backend, messages, schema, *a, **kw)

    with tempfile.TemporaryDirectory() as d, rendering(Path(d) / "a.mp4"):
        tiny_clip(Path(d) / "a.mp4")
        config = _config(video_enabled=True, clip_seconds=1)
        player = Scripted()
        held: list[float] = []
        play = player.play

        async def spy_play(item):
            held.append(item.duration)
            await play(item)
        player.play = spy_play
        with mock.patch.object(Clips, "synthesize", spy_render), \
             mock.patch.object(MockBackend, "generate_structured", spy_llm):
            state = await runner.run_live(config=config, story=story, log_dir=d, player=player, max_segments=3)
        world_dir = next((Path(d) / "sessions").iterdir())
        records = [json.loads(x) for x in (world_dir / "timing.jsonl").read_text().splitlines()]
        from src.story.world import WorldModel
        world = WorldModel.load(world_dir)

        wrong = _config(video_enabled=True, clip_seconds=2)
        await runner.run_live(config=wrong, story=story, log_dir=Path(d) / "wrong", player=Scripted(), max_segments=1)
        wrong_records = [json.loads(x) for x in next((Path(d) / "wrong" / "sessions").iterdir())
                         .joinpath("timing.jsonl").read_text().splitlines()]
    one = 1.0
    assert [s.render_frames for s in shapes_seen] == [33, 33, 33]
    assert held == [one] * 3 and [h.shot.duration_seconds for h in state.history] == [one] * 3
    assert [e.duration for e in world.events] == [one] * 3
    assert all("clips of 1 seconds" in s for s in systems), "Now is told the shape's length"
    assert not [r for r in records if r["event"] == "shape_mismatch"]
    mismatch = [r for r in wrong_records if r["event"] == "shape_mismatch"]
    assert mismatch and mismatch[0]["frames"] == 57 and abs(mismatch[0]["measured_s"] - 33 / 24) < 0.01


def test_a_reference_is_resized_whole_or_refused():
    """A reference at the render aspect to the pixel is resized whole (no
    crop, no padding, its edges kept); anything else is refused before the
    film starts."""
    from src.i2v.ltxv import ReferenceRefused, fit_reference
    story = FrameZero.from_json("tests/fixtures/lego-void")
    png, notes = fit_reference(story.reference_image, 512, 288)
    assert Image.open(io.BytesIO(png)).size == (512, 288) and notes == [], "the story's own reference fits"
    with tempfile.TemporaryDirectory() as d:
        edge = Image.new("RGB", (1920, 1080), "white")
        for x in range(1920):
            edge.putpixel((x, 0), (0, 0, 255))              # a blue top row: cropped by a cover, kept here
        edge.save(Path(d) / "edge.png")
        png, notes = fit_reference(Path(d) / "edge.png", 512, 288)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        assert img.size == (512, 288) and notes == [] and img.getpixel((256, 0))[2] > 150
        for size in ((1672, 930), (380, 381)):              # 11 px off 16:9, and a square
            Image.new("RGB", size, "red").save(Path(d) / "off.png")
            try:
                fit_reference(Path(d) / "off.png", 512, 288)
                raise AssertionError(f"{size} must be refused")
            except ReferenceRefused as exc:
                assert "1920x1080" in str(exc)
