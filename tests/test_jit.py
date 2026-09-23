"""The just-in-time schedule of the live config holds under simulated time.

`tests/jit_sim.py` runs `configs/director.live.yaml` with the mock
Director and replayed clips, compressed in time, with render walls and
Director calls drawn from the runtime baseline. These tests assert what
the current policy promises: no stall, the steady-state buffer it is
built to keep, and a viewer line that reaches the earliest clip not yet
rendering at a bounded cost to the buffer, even when it arrives just
before a render fire or in the middle of a Director call.
"""

from __future__ import annotations

import random
import statistics

from src.eval import runner
from tests.jit_sim import Line, Spread, baseline_director, baseline_render, clip_length, simulate

# Real-time seconds a measured margin may differ from the policy's arithmetic
# (timer jitter multiplied by the time scale, and render-to-render spread).
TOLERANCE = 0.3


def _lines() -> list[Line]:
    """One line in the middle of a Director call, three sent 0.9, 0.6 and
    0.3 s before a render fire, one 0.3 s after one."""
    return [
        Line("the ground starts to tilt", after_clip=8, mid_call=0.6),
        Line("a paper plane drifts past", after_clip=10, before_fire=0.9),
        Line("he starts to hum", after_clip=12, before_fire=0.6),
        Line("the light turns warm", after_clip=14, before_fire=0.3),
        Line("a small cloud forms overhead", after_clip=16, before_fire=-0.3),
    ]


def test_draws_are_clipped_and_reproducible():
    spread = baseline_director()
    draws = [spread.draw(random.Random(f"0:director:{n}:1")) for n in range(500)]
    assert all(spread.lo <= d <= spread.hi for d in draws)
    assert spread.hi == 4.37, "capped at the baseline's slowest call"
    assert draws == [spread.draw(random.Random(f"0:director:{n}:1")) for n in range(500)]
    assert abs(statistics.mean(draws) - spread.mean) < 0.1
    render = baseline_render()
    assert render.hi < clip_length(), "the baseline renders under real time"


async def test_the_live_schedule_holds_the_picture_under_adversarial_lines():
    sim = await simulate(clips=34, lines=_lines())
    film = sim.measured["film"]

    # The gates the timing log can judge.
    for gate in ("stalls", "first_picture_s", "mid_film_compiles", "steering_p50_s", "steering_p95_s",
                 "render_ratio_p50", "render_ratio_p95", "render_ratio_max"):
        assert sim.gate(gate)["result"] == "PASS", sim.gate(gate)

    emits, fires = sim.by_turn("emit"), sim.by_turn("fire")
    margins = {r["turn"]: r["margin"] for r in sim.of("enqueued") if r["margin"] is not None}
    received = {r["line"]: r for r in sim.of("line_received")}
    landed = {line: r["turn"] for r in sim.of("line_landed") for line in r["lines"]}
    plans: dict[int, list[dict]] = {}
    for r in sim.of("director_end"):
        plans.setdefault(r["turn"], []).append(r)

    # The buffer the policy is built to keep: a render fires the estimate
    # times the safety factor, a margin and the slack before its clip is
    # due, so the clip is queued the estimate's surplus plus both before air.
    renders = [r["render_s"] for r in sim.of("render_done")]
    buffer = ((runner.JIT_SAFETY - 1) * statistics.mean(renders) + runner.JIT_MARGIN_S
              + sim.config.render_slack_seconds)
    first_line = min(r["t"] for r in received.values())
    steady = [turn for turn, r in emits.items()
              if r["clip"] > 1 and turn > 3 and fires[turn]["t"] < first_line and turn in margins]
    assert len(steady) >= 3, steady
    for turn in steady:
        assert abs(margins[turn] - buffer) <= TOLERANCE, (turn, margins[turn], buffer)
    assert buffer - TOLERANCE >= 1.0, "the steady buffer clears the 1 s minimum-margin gate"

    # Every line was sent, reached the screen, and was not dropped.
    assert all(line.id is not None for line in sim.lines), [(x.text, x.id) for x in sim.lines]
    assert film["lines_dropped"] == [] and film["lines_unlanded"] == [], film
    for line in sim.lines:
        got = received[line.id]
        turn = landed[line.id]
        # The earliest clip not yet emitted when the line arrived.
        assert turn == min(t for t, r in emits.items() if r["t"] > got["t"]), (line.text, turn)
        # A line costs the buffer at most the Director time spent on the
        # clip it lands on.
        spent = sum(p["plan_s"] for p in plans[turn])
        assert margins[turn] >= buffer - spent - TOLERANCE, (line.text, margins[turn], buffer, spent)

        if line.before_fire is not None and line.before_fire > 0:
            # Sent just before a fire: it lands two clips after the one on
            # air, the fire waits for the answer, and the buffer it spends
            # says when it arrived.
            assert turn == got["on_air"] + 2, (line.text, got["on_air"], turn)
            answer = next(p for p in plans[turn] if p.get("input") and not p.get("cancelled"))
            assert fires[turn]["t"] - answer["t"] <= TOLERANCE, (line.text, "the fire did not wait for the answer")
            early = margins[turn] - (buffer - answer["plan_s"])
            assert abs(early - line.before_fire) <= TOLERANCE, (line.text, early)
        elif line.before_fire is not None:
            # Sent just after a fire: one clip later, and it costs nothing.
            assert turn == got["on_air"] + 3, (line.text, got["on_air"], turn)
            assert margins[turn] >= buffer - TOLERANCE, (line.text, margins[turn], buffer)
        else:
            # Sent while the Director wrote the next beat: that call is
            # cancelled and the restarted one answers the line.
            assert any(p.get("cancelled") for p in plans[turn]), plans[turn]
            assert any(p.get("input") and not p.get("cancelled") for p in plans[turn]), plans[turn]


async def test_the_simulation_reports_a_stall_when_the_renderer_is_slower_than_real_time():
    length = clip_length()
    slow = Spread(mean=1.5 * length, sd=0.05, hi=1.5 * length + 0.15)
    sim = await simulate(clips=6, render=slow)
    assert sim.gate("stalls")["result"] == "FAIL", sim.gate("stalls")
    assert sim.measured["film"]["stalls"] >= 1
    assert sim.gate("render_ratio_p95")["result"] == "FAIL"


async def test_a_second_line_after_the_answer_is_joined():
    """ARCH-06: a line that arrives after the Director has answered the
    previous one, but before that answer has played, is joined to it: the
    pair is planned once, recorded as one intervention with one
    disposition, and lands on one clip; nothing is dropped."""
    first = "A soccer ball appears at your feet."
    second = "Make it bounce."
    sim = await simulate(clips=16, lines=[Line(first, after_clip=6, before_fire=-0.3),
                                          Line(second, after_clip=6, after_answer=0.4)])
    assert all(line.id is not None for line in sim.lines), [(x.text, x.id) for x in sim.lines]
    assert [(i.text, i.disposition) for i in sim.world.interventions] == [(f"{first} {second}", "absorbed")], \
        [(i.text, i.disposition) for i in sim.world.interventions]
    assert not sim.of("line_dropped"), sim.of("line_dropped")
    landed = sim.of("line_landed")
    assert len(landed) == 1 and sorted(landed[0]["lines"]) == [1, 2], landed
    answers = [r for r in sim.of("director_end") if r["input"] and not r.get("cancelled")]
    assert len(answers) == 2, "the first answer, then one call for the pair"
    assert sim.measured["film"]["stalls"] == 0
