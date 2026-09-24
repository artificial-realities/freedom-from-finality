"""ARCH-20 (D-06): a viewer line whose answering Director call fails is not
lost. The picture holds for one clip, as before, and the next plan answers
the line, recorded once."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from src.llm.mock_backend import MockBackend
from src.models.story import Story
from tests.test_engine import Scripted, _config

LINE = "a soccer ball rolls in"


def _failing_once():
    """The mock backend, with the first Director call that answers LINE
    failing the way `OpenAIBackend` reports a timeout (BATCH_023): an empty
    response and the error in the usage."""
    generate = MockBackend.generate_structured
    seen = {"failed": 0, "answered": []}

    async def flaky(backend, messages, schema, *a, **kw):
        user = messages[-1]["content"]
        if schema.__name__ == "BeatPlan" and f'Viewer input for this beat: "{LINE}"' in user:
            if not seen["failed"]:
                seen["failed"] += 1
                return "", {"error": "Request timed out."}
            seen["answered"].append(user)
        return await generate(backend, messages, schema, *a, **kw)

    return flaky, seen


async def test_the_live_loop_answers_the_line_at_the_next_plan():
    from src.eval.runner import run_live
    story = Story.from_json("stories/lego-void")
    flaky, seen = _failing_once()
    with tempfile.TemporaryDirectory() as d, mock.patch.object(MockBackend, "generate_structured", flaky):
        state = await run_live(config=_config(), story=story, log_dir=d, player=Scripted({2: LINE}), max_segments=7)
        session = next((Path(d) / "sessions").iterdir())
        records = [json.loads(x) for x in (session / "timing.jsonl").read_text().splitlines()]
        from src.story.world import WorldModel
        world = WorldModel.load(session)
    assert seen["failed"] == 1 and len(seen["answered"]) == 1, "failed once, then answered once"
    mine = [i for i in world.interventions if LINE in i.text]
    assert len(mine) == 1 and mine[0].disposition == "absorbed", [(i.segment, i.text, i.disposition) for i in world.interventions]
    requeued = [r for r in records if r["event"] == "line_requeued"]
    assert requeued and requeued[0]["reason"] == "the Director call that answered them failed"
    held = [e for e in world.events if e.segment == requeued[0]["turn"]]
    assert held and held[0].beat_intent == "holding the picture", "the failed plan held the picture for a clip"
    assert mine[0].segment == requeued[0]["turn"] + 1, "answered by the next plan"
    assert "soccer_ball" in world.entities, "the ball arrived after all"


async def test_the_terminal_loop_carries_the_line_to_the_next_turn():
    from src.eval.runner import run_play

    class UI:
        def __init__(self):
            self.inputs = ["", LINE, "", "", ""]

        async def prompt_for_input(self):
            if not self.inputs:
                raise KeyboardInterrupt
            return self.inputs.pop(0)

        def __getattr__(self, name):          # every display call is a no-op
            return lambda *a, **k: None

    story = Story.from_json("stories/lego-void")
    flaky, seen = _failing_once()
    with tempfile.TemporaryDirectory() as d, mock.patch.object(MockBackend, "generate_structured", flaky):
        try:
            await run_play(config=_config(), story=story, log_dir=d, ui=UI())
        except KeyboardInterrupt:
            pass
        session = next((Path(d) / "sessions").iterdir())
        from src.story.world import WorldModel
        world = WorldModel.load(session)
    assert seen["failed"] == 1 and len(seen["answered"]) == 1
    mine = [i for i in world.interventions if LINE in i.text]
    assert len(mine) == 1 and mine[0].disposition == "absorbed"
