"""The story loop end to end on the scripted mock LLM: beats emitted clip by
clip, viewer lines cutting beats short, Then off the critical path, and the
two failure paths that must not lose the story or the viewer's line."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from src.i2v.base import I2VBackend
from src.llm.mock_backend import MockBackend
from src.models.config import Config
from src.models.story import FrameZero
from src.playback.player import Player
from src.runner import run_live
from src.story.world import WorldModel


class Scripted(Player):
    """Plays instantly and speaks a given line when a given clip starts."""

    def __init__(self, lines: dict[int, str] | None = None, on_prepare: dict[int, str] | None = None) -> None:
        super().__init__(time_scale=1000)
        self.lines = dict(lines or {})
        self.on_prepare = dict(on_prepare or {})
        self.turns: list[int] = []

    def prepare(self, item) -> None:
        if item.turn in self.on_prepare:
            self.push_input(self.on_prepare.pop(item.turn))

    async def play(self, item) -> None:
        self.turns.append(item.turn)
        if item.turn in self.lines:
            self.push_input(self.lines.pop(item.turn))
        await self._hold(item.duration)


class Clips(I2VBackend):
    def __init__(self, clip: Path, output_dir: Path) -> None:
        self.clip, self.output_dir = Path(clip), Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def synthesize(self, *, turn: int, **_) -> str | None:
        out = self.output_dir / f"turn_{turn:04d}.mp4"
        shutil.copyfile(self.clip, out)
        self.last_render = {"source": self.clip.name}
        return str(out)


def rendering(clip: Path):
    return mock.patch("src.runner.LtxvI2V", lambda *, output_dir, **_: Clips(clip, output_dir))


def tiny_clip(path: Path, frames: int = 33) -> Path:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x32:r=24",
                    "-frames:v", str(frames), "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _config(**over) -> Config:
    base = dict(name="mocktest", llm_backend="mock",
                compaction_interval=4, then_min_gap=1, buffer_seconds=0.0)
    base.update(over)
    return Config(**base)


def _records(session: Path) -> list[dict]:
    return [json.loads(x) for x in (session / "timing.jsonl").read_text().splitlines()]


async def test_now_engine_end_to_end():
    fz = FrameZero.from_json("tests/fixtures/lego-void")
    player = Scripted(lines={1: "A soccer ball appears at your feet.", 4: "Do a trick with it."})
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(), story=fz, log_dir=d, player=player, max_segments=7)

        assert player.turns == [1, 2, 3, 4, 5, 6, 7]
        assert len(state.history) == 7, "every clip produced beat + shot"
        inputs = [h.user_input for h in state.history]
        assert inputs == ["", "", "A soccer ball appears at your feet.", "", "Do a trick with it.", "", ""], inputs
        assert ('Lego Man says in a quiet, earnest, slightly formal, a little breathless voice, in English, "Hello? Anyone?"'
                in state.history[0].shot.i2v_prompt), "the line is compiled into its clip, in the character's voice"
        assert "says" not in state.history[1].shot.i2v_prompt, "clip 2 of the beat is silent"
        assert state.history[0].beat.narration.startswith("Lego Man performs action 1."), "the clip's action is the prose"
        assert state.history[1].beat.narration == "He holds still. Nothing else happens.", "clip 2 of the beat, no LLM call"

        transcript = next(Path(d).glob("*.md")).read_text()
        session_dir = next((Path(d) / "sessions").iterdir())
        world = WorldModel.load(session_dir)
        assert len(world.events) == 7
        assert [(e.beat, e.clip_index, e.clip_count) for e in world.events[:3]] == [(1, 1, 2), (1, 2, 2), (2, 1, 1)]
        assert world.events[0].dialogue == ["Lego Man: Hello? Anyone?"] and world.events[1].dialogue == []
        assert "soccer_ball" in world.entities
        ball = world.entities["soccer_ball"]
        assert ball.state == "rolled out of frame" and not ball.present
        disp = [(i.text[:12], i.disposition, i.resolved_at) for i in world.interventions]
        assert disp == [("A soccer bal", "absorbed", 3), ("Do a trick w", "absorbed", 6)], disp
        hero = world.entities["lego_man"]
        assert hero.state == "state 7", "the state the last op wrote"
        assert "cut short" in transcript and "the viewer spoke" in transcript
        assert world.then_runs >= 2, "bootstrap + at least one compaction"
        assert world.summaries, "compaction landed"

        assert 'Beat 1 "he wakes and looks around" · clip 1 of 2' in transcript and "clip 2 of 2" in transcript
        assert "**World**" in transcript and "**Dialogue**\n\n> Lego Man: Hello? Anyone?" in transcript


async def test_viewer_line_cuts_a_beat_short():
    fz = FrameZero.from_json("tests/fixtures/lego-void")
    player = Scripted(on_prepare={1: "A soccer ball appears at your feet."})
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(), story=fz, log_dir=d, player=player, max_segments=3)
        transcript = next(Path(d).glob("*.md")).read_text()
        world = WorldModel.load(next((Path(d) / "sessions").iterdir()))
    assert [h.user_input for h in state.history] == ["", "A soccer ball appears at your feet.", ""]
    assert [(e.beat, e.clip_index) for e in world.events[:2]] == [(1, 1), (2, 1)]
    assert "cut short" in transcript and "the viewer spoke" in transcript
    assert "soccer_ball" in world.entities


async def test_then_never_blocks_a_segment():
    import time
    fz = FrameZero.from_json("tests/fixtures/lego-void")
    MockBackend.then_delay = 0.25
    try:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory() as d:
            state = await run_live(config=_config(compaction_interval=2), story=fz, log_dir=d,
                                   player=Scripted(lines={1: "A soccer ball appears at your feet.", 4: "Do a trick with it."}),
                                   max_segments=7)
            wall = time.monotonic() - t0
            transcript = next(Path(d).glob("*.md")).read_text()
            world = WorldModel.load(next((Path(d) / "sessions").iterdir()))
    finally:
        MockBackend.then_delay = 0.0
    assert len(state.history) == 7
    assert wall < 0.9, wall
    assert "still running; continuing with the current notebook" in transcript
    assert "then trigger held" in transcript and "still in flight" in transcript
    assert world.summaries, "the compaction landed by the end of the run"


async def test_a_failed_render_rolls_the_world_back_and_the_film_goes_on():
    fz = FrameZero.from_json("tests/fixtures/lego-void")
    synthesize, generate = Clips.synthesize, MockBackend.generate_structured
    left, prompts = {3: 1}, []

    async def flaky(backend, *, turn, **kw):
        if left.get(turn, 0) > 0:
            left[turn] -= 1
            backend.last_render = {"failed": True}
            return None
        return await synthesize(backend, turn=turn, **kw)

    async def spy(backend, messages, schema, *a, **kw):
        if schema.__name__ == "BeatPlan":
            prompts.append(messages[-1]["content"])
        return await generate(backend, messages, schema, *a, **kw)

    with tempfile.TemporaryDirectory() as d:
        clip = tiny_clip(Path(d) / "a.mp4")
        config = _config(video_enabled=True, clip_seconds=1, compaction_interval=100)
        player = Scripted()
        with rendering(clip), mock.patch.object(Clips, "synthesize", flaky), \
             mock.patch.object(MockBackend, "generate_structured", spy):
            state = await run_live(config=config, story=fz, log_dir=d, player=player, max_segments=5)
        session = next((Path(d) / "sessions").iterdir())
        records, world = _records(session), WorldModel.load(session)
        transcript = next(Path(d).glob("*.md")).read_text()
    assert player.turns == [1, 2, 3, 4, 5], "the viewer sees every segment once"
    assert [e.segment for e in world.events] == [1, 2, 3, 4, 5], "the rolled-back clip left no record"
    assert [h.turn for h in state.history] == [1, 2, 3, 4, 5]
    rollback = [r for r in records if r["event"] == "rollback"]
    assert [r["turn"] for r in rollback] == [3] and rollback[0]["reason"] == "its render failed"
    retry = [r for r in records if r["event"] == "render_retry"]
    assert retry and retry[0]["retry"] is False, "no retry when it could not land before the clip is due"
    replan = next(p for p in prompts if "never reached the viewer" in p)
    assert replan.startswith("Segment 3") and "The picture ends on: Lego Man standing still" in replan, "clip 2's end state"
    assert "Segment 3 never reached the viewer (its render failed)" in transcript
    assert not [r for r in records if r["event"] == "error"]


async def test_a_line_whose_answer_failed_is_answered_at_the_next_plan():
    line = "a soccer ball rolls in"
    generate = MockBackend.generate_structured
    seen = {"failed": 0, "answered": 0}

    async def flaky(backend, messages, schema, *a, **kw):
        if schema.__name__ == "BeatPlan" and f'Viewer input for this beat: "{line}"' in messages[-1]["content"]:
            if not seen["failed"]:
                seen["failed"] += 1
                return "", {"error": "Request timed out."}
            seen["answered"] += 1
        return await generate(backend, messages, schema, *a, **kw)

    fz = FrameZero.from_json("tests/fixtures/lego-void")
    with tempfile.TemporaryDirectory() as d, mock.patch.object(MockBackend, "generate_structured", flaky):
        await run_live(config=_config(), story=fz, log_dir=d, player=Scripted({2: line}), max_segments=7)
        session = next((Path(d) / "sessions").iterdir())
        records, world = _records(session), WorldModel.load(session)
    assert seen == {"failed": 1, "answered": 1}, "failed once, then answered once"
    mine = [i for i in world.interventions if line in i.text]
    assert len(mine) == 1 and mine[0].disposition == "absorbed"
    requeued = [r for r in records if r["event"] == "line_requeued"]
    assert requeued and requeued[0]["reason"] == "the Now call that answered them failed"
    held = [e for e in world.events if e.segment == requeued[0]["turn"]]
    assert held and held[0].beat_intent == "holding the picture", "the failed plan held the picture for a clip"
    assert mine[0].segment == requeued[0]["turn"] + 1, "answered by the next plan"
