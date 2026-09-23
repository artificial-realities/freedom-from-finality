"""Drain before emit (RUN-07) and fail-soft rendering (RUN-09): a failed
render is retried once when the buffer allows, otherwise the world and the
beat are rolled back to before the failed clip and the Director replans
from the last clip the viewer received; an exception while emitting takes
the same route; repeated failures stop the film instead of looping."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from src.eval.runner import run_live
from src.i2v.replay import ReplayI2V
from src.llm.mock_backend import MockBackend
from src.models.config import Config
from src.models.story import Story
from src.story.world import WorldModel
from tests.test_engine import Scripted

STORY = "stories/lego-void"


def _config(**over) -> Config:
    base = dict(name="failsoft", graph="director", llm_backend="mock", model="mock", buffer_seconds=0.0,
                video_enabled=True, clip_seconds=1, i2v_backend="replay", compaction_interval=100)
    base.update(over)
    return Config(**base)


def _clip_dir(d: str) -> str:
    clips = Path(d) / "clips"
    clips.mkdir()
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x32:r=24",
                    "-frames:v", "25", "-pix_fmt", "yuv420p", str(clips / "a.mp4")], check=True)
    return str(clips)


async def _run(fail: dict[int, int] | None = None, *, always_fail: bool = False, lines=None, segments=5,
               raise_on_compile: int | None = None, **over):
    """A replay run where render `turn` fails its first `fail[turn]` attempts
    (or every render fails); returns the run's records, world, transcript,
    state, played turns and the Director's user prompts."""
    story = Story.from_json(STORY)
    left = dict(fail or {})
    synthesize, generate = ReplayI2V.synthesize, MockBackend.generate_structured
    prompts: list[str] = []

    async def flaky(backend, *, turn, **kw):
        if always_fail or left.get(turn, 0) > 0:
            left[turn] = left.get(turn, 0) - 1
            backend.last_render = {"failed": True}
            return None
        return await synthesize(backend, turn=turn, **kw)

    async def spy(backend, messages, schema, *a, **kw):
        if schema.__name__ == "BeatPlan":
            prompts.append(messages[-1]["content"])
        return await generate(backend, messages, schema, *a, **kw)

    from src.graph import director_engine
    compile_clip = director_engine.compile_clip
    raised = []

    def breaking_compile(beat, index, world, **kw):
        if kw.get("segment_no") == raise_on_compile and not raised:
            raised.append(True)
            raise ValueError("a compiler bug")
        return compile_clip(beat, index, world, **kw)

    with tempfile.TemporaryDirectory() as d:
        config = _config(i2v_replay_dir=_clip_dir(d), **over)
        player = Scripted(lines=lines or {})
        with mock.patch.object(ReplayI2V, "synthesize", flaky), \
             mock.patch.object(MockBackend, "generate_structured", spy), \
             mock.patch.object(director_engine, "compile_clip", breaking_compile):
            state = await run_live(config=config, story=story, log_dir=d, player=player, max_segments=segments)
        session = next((Path(d) / "sessions").iterdir())
        records = [json.loads(x) for x in (session / "timing.jsonl").read_text().splitlines()]
        world = WorldModel.load(session)
        transcript = next(Path(d).glob("*.md")).read_text()
    return records, world, transcript, state, player.turns, prompts


def _of(records, event):
    return [r for r in records if r["event"] == event]


def _index(records, event, turn):
    return next(i for i, r in enumerate(records) if r["event"] == event and r.get("turn") == turn)


async def test_the_previous_render_is_known_before_the_next_clip_is_emitted():
    """RUN-07: for every clip, its predecessor's render_done comes before its emit."""
    records, *_ = await _run(segments=5)
    for turn in range(2, 6):
        assert _index(records, "render_done", turn - 1) < _index(records, "emit", turn), turn


async def test_a_failed_render_rolls_the_world_back_and_the_next_clip_continues_from_the_one_before():
    """RUN-09's validation: render 3 fails and there is no time to retry it
    (at 1000x the clip is due in milliseconds) → the world and the beat are
    restored to before clip 3, the Director replans segment 3 told that the
    picture ends on clip 2, and the film goes on."""
    records, world, transcript, state, played, prompts = await _run({3: 1}, segments=5)
    assert played == [1, 2, 3, 4, 5], "the viewer sees every segment once"
    assert [e.segment for e in world.events] == [1, 2, 3, 4, 5], "the rolled-back clip left no record"
    assert [h.turn for h in state.history] == [1, 2, 3, 4, 5]
    rollback = _of(records, "rollback")
    assert [r["turn"] for r in rollback] == [3] and rollback[0]["reason"] == "its render failed"
    failed = [r for r in _of(records, "render_done") if r["turn"] == 3 and not r["ok"]]
    assert len(failed) == 1 and _of(records, "render_retry")[0]["retry"] is False, \
        "no retry when it could not land before the clip is due"
    replan = next(p for p in prompts if "never reached the viewer" in p)
    assert replan.startswith("Segment 3") and "The picture ends on: Lego Man standing still" in replan, "clip 2's end state"
    assert "Segment 3 never reached the viewer (its render failed)" in transcript
    emits = [r for r in _of(records, "emit") if r["turn"] == 3]
    assert len(emits) == 2 and emits[1]["beat"] > emits[0]["beat"], "segment 3 again, from a new beat"
    assert not [r for r in _of(records, "error")]


async def test_a_render_is_retried_once_when_there_is_time():
    """While the lead builds nothing is due yet: a failed first render is
    retried and the film is unchanged."""
    records, world, _, _, played, _ = await _run({1: 1}, segments=3)
    first = [r for r in _of(records, "render_done") if r["turn"] == 1]
    assert [(r["ok"], r["attempt"]) for r in first] == [(False, 1), (True, 2)]
    assert _of(records, "render_retry")[0]["retry"] is True and not _of(records, "rollback")
    assert played == [1, 2, 3] and [e.segment for e in world.events] == [1, 2, 3]


async def test_an_emit_exception_is_rolled_back_instead_of_ending_the_film():
    records, world, transcript, state, played, _ = await _run(segments=5, raise_on_compile=3)
    assert [r["turn"] for r in _of(records, "emit_failed")] == [3]
    assert "a compiler bug" in _of(records, "emit_failed")[0]["error"]
    assert played == [1, 2, 3, 4, 5] and [e.segment for e in world.events] == [1, 2, 3, 4, 5]
    assert "Segment 3 could not be emitted" in transcript


async def test_repeated_failures_stop_the_film_instead_of_looping():
    records, world, _, _, played, _ = await _run(always_fail=True, segments=20)
    errors = _of(records, "error")
    assert errors and "3 segments failed in a row" in errors[0]["error"]
    assert len(_of(records, "rollback")) <= 3 and played == []


async def test_without_fail_soft_a_failed_render_is_skipped():
    """The rollback flag: the old behaviour (the clip is skipped; the world
    keeps it; no retry)."""
    records, world, _, _, played, _ = await _run({3: 1}, segments=5, fail_soft=False)
    assert [r["turn"] for r in _of(records, "render_skipped")] == [3]
    assert not _of(records, "rollback") and not _of(records, "render_retry")
    assert 3 not in played and [e.segment for e in world.events] == [1, 2, 3, 4, 5]


async def test_a_line_whose_answer_never_rendered_is_answered_again():
    """The line landed on the clip that failed: it is re-queued and the
    replanned segment answers it; it is recorded once."""
    line = "A soccer ball appears at your feet."
    records, world, _, state, played, prompts = await _run({3: 1}, segments=5, lines={1: line})
    assert [r["turn"] for r in _of(records, "rollback")] == [3]
    assert _of(records, "line_requeued") and _of(records, "line_requeued")[0]["lines"] == [1]
    assert [(i.text, i.disposition) for i in world.interventions] == [(line, "absorbed")]
    assert [h.user_input for h in state.history].count(line) == 1
    assert "soccer_ball" in world.entities and world.entities["soccer_ball"].first_seen == 3
    landed = [r for r in _of(records, "line_landed")]
    assert [r["turn"] for r in landed] == [3, 3], "landed, rolled back, landed again"
