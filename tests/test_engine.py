"""End-to-end engine runs on the scripted mock backend: both engines
drive the real runner, transcript, persistence and history."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.eval.runner import run_scenario
from src.models.config import Config
from src.models.story import Story
from src.story.world import WorldModel

SCENARIO = ["Look around.", "A soccer ball appears at your feet.", "", "Do a trick with it.", "", ""]


def _config(**over) -> Config:
    base = dict(name="mocktest", graph="director", llm_backend="mock", model="mock",
                min_pause_seconds=6.0, compaction_interval=4, stagnation_threshold=3)
    base.update(over)
    return Config(**base)


async def test_director_engine_end_to_end():
    story = Story.from_json("data/story.json")
    with tempfile.TemporaryDirectory() as d:
        scenario = Path(d) / "scenario.json"
        scenario.write_text(json.dumps(SCENARIO))
        state = await run_scenario(config=_config(), story=story, scenario_path=scenario, log_dir=d)

        assert len(state.history) == len(SCENARIO), "every segment produced beat + shot + commentary"
        assert state.history[0].commentary.voiceover == "From the stillness, he stirs."
        assert state.history[1].commentary.voiceover == "", "floor closed the segment after a line"
        assert "voice-over (English" in state.history[0].shot.i2v_prompt, "ltx narration folded into the prompt"

        session_dir = next((Path(d) / "sessions").iterdir())
        world = WorldModel.load(session_dir)
        assert len(world.events) == len(SCENARIO)
        assert "soccer_ball" in world.entities and "lego_woman" not in world.entities
        ball = world.entities["soccer_ball"]
        assert ball.status == "rolled out of frame" and not ball.present and ball.holder == ""
        assert world.threads[0].status == "paid_off"
        disp = [i.disposition for i in world.interventions]
        # Turn 1's input got no disposition from the (scripted) Director, so the
        # validator recorded it as deferred; turn 4 was deferred and then
        # absorbed at turn 5 when the Director said so without new input.
        assert disp == ["deferred", "absorbed", "absorbed"], disp
        assert world.interventions[2].resolved_at == 5, "deferred at 4, absorbed at 5"
        assert world.interventions[0].resolved_at is None
        assert world.showrunner_runs >= 3, "bootstrap + compaction at 4 + scene_done at 5"
        assert world.plan.scene.id >= 2 and world.plan.arc, "the Showrunner replanned after scene_done"
        assert world.summaries and world.act_summary, "compaction landed"
        assert world.entities["lego_man"].notes == ["He is proud of the kick."]

        transcript = next(Path(d).glob("*.md")).read_text()
        assert "first proposal rejected" in transcript and "retry accepted" in transcript
        assert "Transition: cut" in transcript or "Shots: 2 (cut, continue)" in transcript
        assert "Showrunner spawned: scene_done" in transcript
        assert "showrunner (bootstrap" in transcript and "scene 1 card" in transcript
        assert "**World**" in transcript


async def test_legacy_engine_still_runs():
    story = Story.from_json("data/story.json")
    with tempfile.TemporaryDirectory() as d:
        scenario = Path(d) / "scenario.json"
        scenario.write_text(json.dumps(SCENARIO[:3]))
        state = await run_scenario(config=_config(graph="mas_graph"), story=story, scenario_path=scenario, log_dir=d)
        assert len(state.history) == 3
        assert state.history[0].beat.narration == "Mock beat."
        assert state.narrative_memory == "Mock memory."
