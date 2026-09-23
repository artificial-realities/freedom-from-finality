"""The live loop under compressed time: the offline JIT simulation.

`simulate()` runs `run_live` on a real config with the mock LLM and the
replay backend, `scale` times faster than real time. The player holds
each clip for its length over `scale`, and every delay the scheduler
lives by is divided by it: the render and the Director call (drawn from
`benchmarks/runtime/baseline.json`), the Showrunner, the config's
`render_slack_seconds` and the runner's `JIT_MARGIN_S`. The timing log is
multiplied back afterwards, so what `scripts/bench_report.py` measures,
and what a test asserts, is in real-time seconds.

Viewer lines are scripted against the run itself: a line is sent a given
time before (or after) a render fire the loop is about to make, or in the
middle of a Director call. The fire moment is predicted from the fires the
run has already made, not from the policy's formula, so a change of
policy keeps the lines adversarial.

Nothing here is collected as a test (`tests/test_jit.py` is); later
tests and the X-09 experiment import it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from src.eval import runner
from src.i2v.replay import ReplayI2V
from src.llm.mock_backend import MockBackend, _segment_number, _viewer_line
from src.models.config import Config
from src.models.story import Story
from src.playback.player import Playable, Player
from src.story.world import WorldModel

REPO = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO / "benchmarks" / "runtime" / "baseline.json"
LIVE_CONFIG = REPO / "configs" / "director.live.yaml"
STORY = REPO / "stories" / "lego-void"

_spec = importlib.util.spec_from_file_location("bench_report", REPO / "scripts" / "bench_report.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

# Timing-log fields in the loop's seconds; everything else (`wall`, media
# `duration`, story `seconds`) is left alone when the log is scaled back.
SCALED_FIELDS = ("t", "into", "margin", "due_in", "render_s", "hold", "waited", "plan_s", "director_s")


def baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Spread:
    """A delay distribution: a normal clipped to [mean − 3 sd, hi]."""

    mean: float
    sd: float
    hi: float

    @classmethod
    def of(cls, stats: dict) -> "Spread":
        """From a baseline summary (mean, sd, max): capped at the observed
        maximum or three deviations above the mean, whichever is lower."""
        return cls(stats["mean"], stats["sd"], min(stats["max"], stats["mean"] + 3 * stats["sd"]))

    @property
    def lo(self) -> float:
        return max(0.0, self.mean - 3 * self.sd)

    def draw(self, rng: random.Random) -> float:
        return min(self.hi, max(self.lo, rng.gauss(self.mean, self.sd)))


def baseline_render() -> Spread:
    return Spread.of(baseline()["renders"]["render_s"])


def baseline_director() -> Spread:
    return Spread.of(baseline()["director"]["seconds"])


def clip_length() -> float:
    """The baseline shape's delivered length (145 frames at 24 fps)."""
    shape = baseline()["shape"]
    return shape["render_frames"] / shape["fps"]


@dataclass
class Line:
    """A scripted viewer line, sent while clip `after_clip` or a later one
    is on air, once every earlier line has reached the screen.

    `before_fire` sends it that many real-time seconds before the next
    render fire (negative: after it), at the first clip whose fire is a
    continuation of a beat (a fire on the just-in-time schedule, not one
    brought forward by a Director call). `mid_call` sends it instead
    `mid_call` of the way through the first Director call that is not
    answering a line (the cancel-and-restart path). `after_answer` sends
    it that many real-time seconds after the Director call answering the
    previous line returns, before that answer has played (the second
    line of a pair)."""

    text: str
    after_clip: int
    before_fire: float | None = None
    mid_call: float | None = None
    after_answer: float | None = None
    # Filled in when it is sent.
    id: int | None = None
    sent_at: float | None = None


class SimPlayer(Player):
    """Holds each clip for its length over the time scale, notes when each
    clip went on air and when each render fired, and sends the scripted
    lines."""

    def __init__(self, *, scale: float, lines: list[Line]) -> None:
        super().__init__(time_scale=scale)
        self.lines = lines
        self.starts: dict[int, float] = {}               # turn → monotonic time on air
        self.fires: dict[int, float] = {}                # turn → monotonic time its render fired
        self.positions: dict[int, tuple[int, int]] = {}  # turn → (clip, clips in its beat)
        self.answered: list[str] = []                    # viewer text each played clip answered

    # What the loop tells the player.

    def prepare(self, item: Playable) -> None:
        index, _, count = (item.clip or "1/1").partition("/")
        self.positions[item.turn] = (int(index), int(count or index))

    async def play(self, item: Playable) -> None:
        self.starts[item.turn] = time.monotonic()
        if item.user_input:
            self.answered.append(item.user_input)
        line = self._next_line(item.turn)
        if line is not None and line.before_fire is not None:
            phase = self._fire_phase()
            if phase is not None:
                wait = phase - line.before_fire / self.time_scale
                if wait > 0:
                    asyncio.get_running_loop().call_later(wait, self._send_before_fire, line, item.turn)
        await self._hold(item.duration)

    # What the patched backends tell the player.

    def fired(self, turn: int) -> None:
        self.fires[turn] = time.monotonic()

    def director_call(self, seconds: float, *, answering: bool) -> None:
        on_air = max(self.starts, default=0)
        line = self._next_line(on_air)
        if line is not None and line.mid_call is not None and not answering:
            asyncio.get_running_loop().call_later(seconds * line.mid_call, self._send, line)
        follower = self._follower() if answering else None
        if follower is not None:
            asyncio.get_running_loop().call_later(seconds + follower.after_answer / self.time_scale,
                                                  self._send, follower)

    # Sending.

    def _next_line(self, on_air: int) -> Line | None:
        """The first unsent line, if every earlier line has played and its
        clip has come."""
        for line in self.lines:
            if line.id is not None:
                if not any(line.text in text for text in self.answered):
                    return None
                continue
            return line if on_air >= line.after_clip else None
        return None

    def _follower(self) -> Line | None:
        """The first unsent `after_answer` line whose predecessor is sent."""
        for before, line in zip(self.lines, self.lines[1:]):
            if line.id is None:
                return line if line.after_answer is not None and before.id is not None else None
        return None

    def _fire_phase(self) -> float | None:
        """How far into clip k the render of clip k + 2 fires when that clip
        continues a beat: the median over the run so far, skipping fires a
        line may have moved."""
        phases = []
        for turn, fired in self.fires.items():
            aired = self.starts.get(turn - 2)
            if aired is None or self.positions.get(turn, (1, 1))[0] < 2:
                continue
            if any(line.sent_at is not None and aired <= line.sent_at <= fired for line in self.lines):
                continue
            phases.append(fired - aired)
        return statistics.median(phases) if phases else None

    def _send_before_fire(self, line: Line, turn: int) -> None:
        """Send if clip `turn` is still on air and the clip queued behind it
        is not the last of its beat (so the next fire continues a beat);
        otherwise the next clip tries again."""
        index, count = self.positions.get(turn + 1, (0, 0))
        if line.id is None and max(self.starts) == turn and 0 < index < count:
            self._send(line)

    def _send(self, line: Line) -> None:
        if line.id is not None:
            return
        line.sent_at = time.monotonic()
        self.push_input(line.text)
        line.id = self._line_no


@dataclass
class Sim:
    """A finished run: its timing log in real-time seconds, what
    `bench_report` measures on it, and the gate rows."""

    records: list[dict]
    measured: dict
    gates: list[dict]
    lines: list[Line]
    player: SimPlayer
    config: Config             # the config as the policy runs it in real time
    world: WorldModel          # the run's world model as saved at the end

    def of(self, event: str) -> list[dict]:
        return [r for r in self.records if r.get("event") == event]

    def by_turn(self, event: str) -> dict[int, dict]:
        return {r["turn"]: r for r in self.of(event)}

    def gate(self, name: str) -> dict:
        return next(row for row in self.gates if row["gate"] == name)


def _clip(path: Path, frames: int, fps: int) -> Path:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x32:r={fps}",
                    "-frames:v", str(frames), "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _real_time(records: list[dict], scale: float) -> list[dict]:
    for record in records:
        for key in SCALED_FIELDS:
            value = record.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                record[key] = value * scale
    return records


async def simulate(
    *,
    clips: int,
    lines: list[Line] = (),
    scale: float = 25.0,
    seed: int = 0,
    config: Path = LIVE_CONFIG,
    render: Spread | None = None,
    director: Spread | None = None,
    showrunner_s: float = 4.5,
) -> Sim:
    """Run `clips` clips of `config` with the mock LLM and replayed clips,
    `scale` times faster than real time. Render walls and Director calls
    are drawn from `render` and `director` (the baseline's by default),
    seeded per segment and attempt, so a cancelled call does not shift the
    draws after it; the Showrunner takes `showrunner_s`."""
    render = render or baseline_render()
    director = director or baseline_director()
    shape = baseline()["shape"]
    story = Story.from_json(STORY)
    real = Config.from_yaml(config)
    player = SimPlayer(scale=scale, lines=list(lines))
    attempts: dict[int, int] = {}

    generate_structured = MockBackend.generate_structured
    synthesize = ReplayI2V.synthesize

    async def timed_director(backend, messages, schema, *args, **kwargs):
        if schema.__name__ == "BeatPlan":
            segment = _segment_number(messages)
            attempts[segment] = attempts.get(segment, 0) + 1
            seconds = director.draw(random.Random(f"{seed}:director:{segment}:{attempts[segment]}")) / scale
            player.director_call(seconds, answering=bool(_viewer_line(messages)))
            await asyncio.sleep(seconds)
        return await generate_structured(backend, messages, schema, *args, **kwargs)

    async def timed_render(backend, *, turn: int, **kwargs):
        player.fired(turn)
        await asyncio.sleep(render.draw(random.Random(f"{seed}:render:{turn}")) / scale)
        return await synthesize(backend, turn=turn, **kwargs)

    async def known_length(path) -> float:
        # ffprobe takes ~30 ms, which would count `scale` times over.
        return clip_length()

    with tempfile.TemporaryDirectory() as d:
        replay = Path(d) / "replay"
        replay.mkdir()
        _clip(replay / "clip.mp4", shape["render_frames"], shape["fps"])
        run = real.model_copy(update={
            "llm_backend": "mock", "model": "mock",
            "i2v_backend": "replay", "i2v_replay_dir": str(replay), "i2v_replay_delay": 0.0,
            "i2v_seed_image": story.seed_image,
            "render_slack_seconds": real.render_slack_seconds / scale,
        })
        with mock.patch.object(MockBackend, "generate_structured", timed_director), \
             mock.patch.object(MockBackend, "showrunner_delay", showrunner_s / scale), \
             mock.patch.object(ReplayI2V, "synthesize", timed_render), \
             mock.patch.object(runner, "JIT_MARGIN_S", runner.JIT_MARGIN_S / scale), \
             mock.patch.object(runner, "probe_duration", known_length):
            await runner.run_live(config=run, story=story, log_dir=d, player=player, max_segments=clips)
        session = next((Path(d) / "sessions").iterdir())
        records = _real_time(bench.load_records(session / "timing.jsonl"), scale)
        world = WorldModel.load(session)

    gates = baseline()["gates"]
    measured = bench.measure(records, warm_after=baseline()["cold_warm"]["warm_after_clips"],
                             stall_s=gates["stalls"]["threshold_s"], compile_factor=gates["mid_film_compiles"]["factor"])
    return Sim(records=records, measured=measured, gates=bench.evaluate(measured, gates), lines=player.lines,
               player=player, config=real, world=world)
