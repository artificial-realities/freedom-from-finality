"""End-to-end engine runs through the live loop on the scripted mock
backend: beats emitted clip by clip, viewer lines cutting beats short,
transcript, persistence and history."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.eval.runner import run_live
from src.models.config import Config
from src.models.story import Story
from src.playback.player import Player
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


def _config(**over) -> Config:
    base = dict(name="mocktest", graph="director", llm_backend="mock", model="mock",
                compaction_interval=4, showrunner_min_gap=1,
                buffer_seconds=0.0)
    base.update(over)
    return Config(**base)


def test_engine_starts_a_fresh_world_even_if_the_session_dir_has_one():
    """CLEAN-01: resume is not built. A `world.json` already in the session
    directory (which cannot happen: every run makes its own directory) is
    not loaded; the world comes from the blueprint."""
    from src.graph.director_engine import DirectorEngine
    from src.llm import build_backend
    from src.state.story_state import StoryState
    from src.story.schemas import EntityOp
    from src.story.validator import apply_clip
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d:
        old = WorldModel.from_story(story)
        from src.story.schemas import BeatPlan, ClipSpec
        beat = BeatPlan(intent="a hat", framing="wide", clips=[ClipSpec(
            action="A hat.", end_state="hat", on_screen=["lego_man"],
            entity_ops=[EntityOp(op="introduce", entity="hat", description="a red cap")])])
        apply_clip(beat, 0, old, segment_no=9)
        old.segment = 9
        old.save(d)
        assert (Path(d) / "world.json").exists()
        engine = DirectorEngine(llm=build_backend("mock", "mock"), config=_config(), session_dir=d)
        state = StoryState.initialize(story, config_name="t")
        world = engine._init_world(state)
        assert world.segment == 0 and "hat" not in world.entities and "lego_man" in world.entities


def test_seed_image_comes_from_the_story():
    """CLEAN-02: the story owns its reference image; the config's
    `i2v_seed_image` is only the fallback for a story without one."""
    from src.eval.runner import seed_image_for
    story = Story.from_json("stories/lego-void")
    assert story.seed_image.endswith("stories/lego-void/legoman-16x9.png") and Path(story.seed_image).exists()
    assert Config(name="t", graph="director", llm_backend="mock", model="mock").i2v_seed_image == ""
    assert seed_image_for(_config(), story) == story.seed_image
    assert seed_image_for(_config(i2v_seed_image="elsewhere.png"), story) == story.seed_image, "the story wins"
    bare = story.model_copy(update={"seed_image": ""})
    assert seed_image_for(_config(i2v_seed_image="elsewhere.png"), bare) == "elsewhere.png"
    assert seed_image_for(_config(), bare) == ""
    for cfg in ("configs/director.yaml", "configs/director.live.yaml", "configs/director.replay.yaml"):
        loaded = Config.from_yaml(cfg)
        assert loaded.i2v_seed_image == "", f"{cfg}: the story's image, not a config path"
        assert loaded.ltxv_negative_prompt == "", f"{cfg}: no negative prompt (LTX-08)"


async def test_live_loop_renders_from_the_story_image():
    """CLEAN-02 through the loop: with no `i2v_seed_image` in the config the
    render backend is handed the story's seed image (main.py no longer
    copies it into the config)."""
    import subprocess
    from unittest import mock
    from src.i2v.replay import ReplayI2V
    story = Story.from_json("stories/lego-void")
    seen: list[str] = []
    synthesize = ReplayI2V.synthesize

    async def spy(backend, *, image_path, **kwargs):
        seen.append(str(image_path))
        return await synthesize(backend, image_path=image_path, **kwargs)

    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=128x80:d=1:r=24",
                        "-pix_fmt", "yuv420p", str(clips / "a.mp4")], check=True)
        config = _config(video_enabled=True, clip_seconds=1, i2v_backend="replay", i2v_replay_dir=str(clips))
        assert config.i2v_seed_image == ""
        with mock.patch.object(ReplayI2V, "synthesize", spy):
            await run_live(config=config, story=story, log_dir=d, player=Scripted(), max_segments=2)
    assert seen and all(path == story.seed_image for path in seen), seen


async def test_director_engine_end_to_end():
    """On the schedule that plans each beat just before its fire (plan_early
    off), so the timeline below is fixed; early planning has its own test."""
    story = Story.from_json("stories/lego-void")
    player = Scripted(lines={1: "A soccer ball appears at your feet.", 4: "Do a trick with it."})
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(plan_early=False), story=story, log_dir=d, player=player, max_segments=7)

        assert player.turns == [1, 2, 3, 4, 5, 6, 7]
        assert len(state.history) == 7, "every clip produced beat + shot + commentary"
        inputs = [h.user_input for h in state.history]
        # A line spoken while clip k plays reaches the plan for k+2: clip k+1
        # was already emitted (and, with video, rendering) when it arrived.
        assert inputs == ["", "", "A soccer ball appears at your feet.", "", "", "Do a trick with it.", ""], inputs
        assert state.history[0].commentary.voiceover == "From the stillness, he stirs."
        assert state.history[1].commentary.voiceover == "", "the line plays over the beat's first clip only"
        assert "voice-over (English" in state.history[0].shot.i2v_prompt, "ltx narration folded into the prompt"
        assert state.history[0].beat.narration.startswith("Lego Man performs action 1."), "the clip's action is the prose"
        assert state.history[1].beat.narration == "He holds still. Nothing else happens.", "clip 2 of the beat, no LLM call"

        transcript = next(Path(d).glob("*.md")).read_text()
        session_dir = next((Path(d) / "sessions").iterdir())
        world = WorldModel.load(session_dir)
        assert len(world.events) == 7
        assert [(e.beat, e.clip_index, e.clip_count) for e in world.events[:3]] == [(1, 1, 2), (1, 2, 2), (2, 1, 1)]
        assert "soccer_ball" in world.entities and "lego_woman" not in world.entities
        ball = world.entities["soccer_ball"]
        assert ball.status == "rolled out of frame" and not ball.present and ball.holder == ""
        assert world.threads[0].status == "paid_off"
        disp = [(i.text[:12], i.disposition, i.resolved_at) for i in world.interventions]
        assert disp == [("A soccer bal", "absorbed", 3), ("Do a trick w", "absorbed", 7)], disp
        hero = world.entities["lego_man"]
        assert hero.posture == "posture 5" and hero.activity == "looking around", "character state persists until an op changes it"
        assert "cut short after clip 1 of 2 (the viewer spoke)" in transcript, "the trick line cut the running beat short"
        assert world.showrunner_runs >= 3, "bootstrap + compaction at 4 + scene_done at 7"
        assert world.plan.scene.id >= 2 and world.plan.arc, "the Showrunner replanned after scene_done"
        assert world.summaries and world.act_summary, "compaction landed"
        assert hero.notes == ["He is proud of the kick."]

        assert "first proposal rejected" in transcript and "retry accepted" in transcript
        assert 'Beat 1 "he wakes and looks around" · clip 1 of 2' in transcript and "clip 2 of 2" in transcript
        assert "- Now: standing; looking around; centre frame" in transcript
        assert "Showrunner spawned: scene_done" in transcript
        assert "showrunner (bootstrap" in transcript and "scene 1 card" in transcript
        assert "**World**" in transcript


async def test_viewer_line_cuts_a_beat_short():
    """A line that arrives while a beat still has unrendered clips drops
    them; the Director writes the next beat from the line and is told
    where the picture actually ends."""
    story = Story.from_json("stories/lego-void")
    player = Scripted(on_prepare={1: "A soccer ball appears at your feet."})
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(), story=story, log_dir=d, player=player, max_segments=3)
        transcript = next(Path(d).glob("*.md")).read_text()
        world = WorldModel.load(next((Path(d) / "sessions").iterdir()))
    assert [h.user_input for h in state.history] == ["", "A soccer ball appears at your feet.", ""]
    assert [(e.beat, e.clip_index, e.clip_count) for e in world.events] == [(1, 1, 2), (2, 1, 1), (3, 1, 1)]
    assert "cut short after clip 1 of 2 (the viewer spoke)" in transcript
    assert "soccer_ball" in world.entities


async def test_fixed_clip_length_sets_every_clip():
    """RUN-01: every clip has the run's shape (clip_seconds on the 8k+1
    frame grid) and every consumer reads it: the shot, the event record,
    the story clock. An old config's i2v_duration maps once onto
    clip_seconds. The Director is told the length in its prompt."""
    from src.agents.director import SYSTEM_PATH
    assert "{clip_seconds}" in SYSTEM_PATH.read_text() and "{max_clips}" in SYSTEM_PATH.read_text()
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(clip_seconds=6), story=story, log_dir=d,
                               player=Scripted(), max_segments=4)
        world = WorldModel.load(next((Path(d) / "sessions").iterdir()))
    assert [h.shot.duration_seconds for h in state.history] == [145 / 24] * 4
    assert [e.duration for e in world.events] == [145 / 24] * 4 and world.scene_seconds() == 4 * 145 / 24
    assert state.current_shape.render_frames == 145
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(i2v_duration=5), story=story, log_dir=d, player=Scripted(), max_segments=2)
    assert [h.shot.duration_seconds for h in state.history] == [121 / 24] * 2


async def test_showrunner_never_blocks_a_segment():
    """A slow Showrunner lands at a later plan; no segment waits for it,
    a trigger that finds a run in flight is held, and the end of the run
    still lands the update."""
    import time
    from src.llm.mock_backend import MockBackend
    story = Story.from_json("stories/lego-void")
    MockBackend.showrunner_delay = 0.25
    try:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory() as d:
            state = await run_live(config=_config(compaction_interval=2), story=story, log_dir=d,
                                   player=Scripted(lines={1: "A soccer ball appears at your feet.", 4: "Do a trick with it."}),
                                   max_segments=7)
            wall = time.monotonic() - t0
            transcript = next(Path(d).glob("*.md")).read_text()
            world = WorldModel.load(next((Path(d) / "sessions").iterdir()))
    finally:
        MockBackend.showrunner_delay = 0.0
    assert len(state.history) == 7
    # The bootstrap is synchronous (0.25 s) and drain waits for the last run
    # (0.25 s); had each later trigger been awaited, the run would have
    # taken over a second.
    assert wall < 0.9, wall
    assert "still running; continuing with the current plan" in transcript
    assert "showrunner trigger held" in transcript and "still in flight" in transcript
    assert world.summaries, "the compaction landed by the end of the run"


async def test_showrunner_min_gap_holds_triggers():
    """Triggers inside the gap are noted, not run."""
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d:
        await run_live(config=_config(compaction_interval=2, showrunner_min_gap=4), story=story, log_dir=d,
                       player=Scripted(lines={1: "A soccer ball appears at your feet.", 4: "Do a trick with it."}),
                       max_segments=7)
        transcript = next(Path(d).glob("*.md")).read_text()
        world = WorldModel.load(next((Path(d) / "sessions").iterdir()))
    assert world.showrunner_runs == 2, world.showrunner_runs
    assert "showrunner trigger held" in transcript and "minimum 4" in transcript


async def test_the_next_beat_is_planned_as_soon_as_the_last_clip_is_out():
    """RUN-15 (D-14): with plan_early the Director call for the next beat
    starts right after the running beat's last clip is emitted, not just
    before the next fire; a line that arrives while that beat is unplayed
    drops it and is answered at once, landing sooner."""
    import json as _json
    story = Story.from_json("stories/lego-void")
    player = Scripted(lines={4: "Do a trick with it."})
    with tempfile.TemporaryDirectory() as d:
        state = await run_live(config=_config(plan_early=True), story=story, log_dir=d, player=player, max_segments=7)
        session_dir = next((Path(d) / "sessions").iterdir())
        records = [_json.loads(x) for x in (session_dir / "timing.jsonl").read_text().splitlines()]
    inputs = [h.user_input for h in state.history]
    assert inputs.index("Do a trick with it.") == 4, inputs          # typed during clip 4, on air at 5
    emits = {r["turn"]: r["t"] for r in records if r["event"] == "emit"}
    starts = [r for r in records if r["event"] == "director_start" and not r.get("input") and r["turn"] > 2]
    # Each plan for a new beat starts right after the previous clip was emitted.
    assert starts and all(r["t"] - emits[r["turn"] - 1] < 0.05 for r in starts), [(r["turn"], r["t"]) for r in starts]
