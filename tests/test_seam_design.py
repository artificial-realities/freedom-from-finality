"""The overlap seam as a production option (D-03: LTX-12, AUD-02, AUD-03),
chosen by the config's `seam`: the shape renders the window on top of the
delivered frames, every consumer holds the delivered seconds, each clip
after a reference-seeded one carries its overlap for the page's cut and
crossfade, speech is marked, the page and the server follow the config,
and a server whose loudness ceiling differs is noticed."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from src.i2v.shape import ClipShape, ShapeRegistry
from src.models.config import Config
from src.models.story import Story
from tests.test_shape import _config


def test_the_overlap_shape_renders_the_window_on_top():
    fade = ShapeRegistry.from_config(_config()).default
    over = ShapeRegistry.from_config(_config(seam="overlap")).default
    assert (fade.render_frames, fade.delivered_frames) == (145, 145)
    assert (over.render_frames, over.delivered_frames, over.overlap_frames) == (153, 144, 9)
    assert over.delivered_seconds == 6.0 and over.overlap_seconds == 0.375
    assert ClipShape.overlapped(4, overlap_frames=1).render_frames == 97, "96 new + 1 on the 8k+1 grid"
    try:
        Config(name="t", llm_backend="mock", model="m", seam="dissolve")
        raise AssertionError("an unknown seam is refused")
    except ValueError:
        pass


def test_the_server_warms_the_overlap_shape():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "c.yaml"
        cfg.write_text(Path("configs/director.live.yaml").read_text() + '\nseam: "overlap"\naudio_ceiling_lufs: -18\n')
        out = subprocess.run([sys.executable, "-m", "src.i2v.shape", str(cfg)], capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "153,153,153"
        live = Config.from_yaml("configs/director.live.yaml")
        assert (live.seam, live.audio_ceiling_lufs) == ("overlap", -18), "rolled out after RUN-14's GPU check"
        assert Config.from_yaml(cfg).audio_ceiling_lufs == -18
    vast = Path("server/vast_run.sh").read_text()
    assert "config_ceiling" in vast and "LTX2_AUDIO_CEILING_LUFS=$v" in vast, "the server's ceiling comes from the config"
    assert "seam=config.seam" in Path("main.py").read_text(), "the page follows the config"


async def test_clips_carry_their_overlap_and_are_held_for_the_new_frames():
    """Through the live loop with replayed 153-frame clips: holds of 6.0 s,
    the first clip (seeded from the reference) without an overlap, every
    later one with 0.375 s, and a clip with speech marked."""
    from src.eval import runner
    from src.llm.mock_backend import MockBackend
    from tests.test_engine import Scripted
    story = Story.from_json("stories/lego-void")
    generate = MockBackend.generate_structured

    async def talkative(backend, messages, schema, *a, **kw):
        text, usage = await generate(backend, messages, schema, *a, **kw)
        if schema.__name__ == "BeatPlan":
            beat = json.loads(text)
            beat["voiceover"] = "A voice."           # the story's narrator gate decides which clips carry it
            text = json.dumps(beat)
        return text, usage

    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x32:r=24",
                        "-frames:v", "153", "-pix_fmt", "yuv420p", str(clips / "a.mp4")], check=True)
        config = _config(video_enabled=True, seam="overlap", i2v_backend="replay", i2v_replay_dir=str(clips))
        player = Scripted()
        seen = []
        play = player.play

        async def spy(item):
            seen.append(item.model_copy())
            await play(item)
        player.play = spy
        with mock.patch.object(MockBackend, "generate_structured", talkative):
            await runner.run_live(config=config, story=story, log_dir=d, player=player, max_segments=4)
        records = [json.loads(x) for x in next((Path(d) / "sessions").iterdir()).joinpath("timing.jsonl").read_text().splitlines()]
    assert [i.duration for i in seen] == [6.0] * 4
    assert [i.overlap_seconds for i in seen] == [0.0, 0.375, 0.375, 0.375]
    assert not [r for r in records if r["event"] == "shape_mismatch"], "a 153-frame file for a 153-frame shape"
    assert all(r.get("frames", 153) == 153 for r in records if r["event"] == "shapes")
    assert [i.speech for i in seen] == [bool(i.voiceover) for i in seen]
    assert any(i.speech for i in seen) and not all(i.speech for i in seen), [i.voiceover for i in seen]


def test_a_different_server_ceiling_is_noticed():
    from src.eval.runner import _check_shapes
    from src.util.timing_log import TimingLog
    shapes = ShapeRegistry.from_config(_config(seam="overlap"))

    class Server:
        def __init__(self, ceiling):
            self.ceiling = ceiling

        def check_shapes(self, registry):
            return {"frames": registry.frames, "warm_frames": registry.frames,
                    **({} if self.ceiling == "unreported" else {"audio_ceiling_lufs": self.ceiling})}

    with tempfile.TemporaryDirectory() as d:
        log = TimingLog(Path(d) / "t.jsonl")
        want = _config(seam="overlap", audio_ceiling_lufs=-18)
        _check_shapes(Server(None), shapes, log, want)
        _check_shapes(Server(-18.0), shapes, log, want)
        _check_shapes(Server("unreported"), shapes, log, want)
        log.close()
        events = [json.loads(x) for x in (Path(d) / "t.jsonl").read_text().splitlines()]
    mismatches = [e for e in events if e["event"] == "ceiling_mismatch"]
    assert mismatches == [{**mismatches[0], "config": -18, "server": None}] and len(mismatches) == 1
