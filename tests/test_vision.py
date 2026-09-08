"""Frame observation: extraction, the vision call on the mock, and the
engine's landing + forced-cut policy."""

from __future__ import annotations

import asyncio
import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from src.graph.director_engine import DirectorEngine
from src.llm.mock_backend import MockBackend
from src.models.config import Config
from src.models.story import Story
from src.state.story_state import StoryState
from src.story.world import WorldModel
from src.vision import extract_frames, observe_clip


def _tiny_video(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=256x160:d=1:r=24",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _config(**over) -> Config:
    base = dict(name="t", graph="director", llm_backend="mock", model="mock",
                observe_frames=True, vision_model="mock", drift_cut_threshold=2,
                min_segments_between_cuts=1)
    base.update(over)
    return Config(**base)


def test_extract_frames_from_clip():
    with tempfile.TemporaryDirectory() as d:
        frames = extract_frames(str(_tiny_video(Path(d) / "c.mp4")))
        assert len(frames) == 2
        for png in frames:
            assert Image.open(io.BytesIO(png)).size == (512, 320), "scaled to 512 wide, aspect kept"


async def test_observe_clip_with_mock():
    story = Story.from_json("data/story.json")
    world = WorldModel.from_blueprint(title=story.title, characters=story.characters,
                                      locations=story.locations, cast_locked=True)
    with tempfile.TemporaryDirectory() as d:
        obs = await observe_clip(video_path=str(_tiny_video(Path(d) / "c.mp4")), segment=3, world=world,
                                 expected_ids=["lego_man"], visual_style=story.visual_style,
                                 llm=MockBackend(), config=_config())
    assert obs is not None and obs.segment == 3 and obs.drift == 3
    assert obs.entities[0].entity == "lego_man" and not obs.entities[0].matches


async def test_engine_lands_observation_and_forces_cut():
    story = Story.from_json("data/story.json")
    cfg = _config()
    state = StoryState.initialize(story, config_name="t")
    engine = DirectorEngine(llm=MockBackend(), config=cfg)
    state.turn_number = 1
    state.user_input = "Look around."
    await engine.ainvoke(state)
    assert state.current_transition == "continue"
    with tempfile.TemporaryDirectory() as d:
        engine.observe(str(_tiny_video(Path(d) / "c.mp4")), 1)
        assert len(engine._observe_tasks) == 1
        await asyncio.gather(*engine._observe_tasks)
        state.turn_number = 2
        state.user_input = ""
        await engine.ainvoke(state)
    world = engine.world
    assert world.picture is not None and world.picture.drift == 3 and world.picture.segment == 1
    assert world.entities["lego_man"].observed_look == "green head with black hair"
    assert state.current_transition == "cut", "drift above threshold forces a cut"
    assert "forced cut" in state.turn_notes and "picture (segment 1): drift 3/3" in state.turn_notes
    assert "not green head with black hair" in state.current_shot.i2v_prompt, "compiler pulls back to the sheet"
    assert "not grey speckled floor" in state.current_shot.i2v_prompt
    assert world.last_cut == 2
    # The next segment does not force again: the drift predates the last cut.
    state.turn_number = 3
    await engine.ainvoke(state)
    assert state.current_transition == "continue"
