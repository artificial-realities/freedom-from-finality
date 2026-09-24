"""Experiment-only arms stay off by default and do what their experiment
needs: RUN-06's deadline admission (X-09), selected only by a config flag
an experiment sets. LTX-01's caption-prose compiler (X-05) was rejected
and removed on 2026-09-23; a config that asks for it is refused."""

from __future__ import annotations

from src.models.config import Config
from tests.jit_sim import Line, simulate


def test_the_arms_are_off_by_default():
    cfg = Config(name="t", llm_backend="mock", model="m")
    assert cfg.jit_policy == "current"
    for path in ("configs/director.yaml", "configs/director.live.yaml", "configs/director.replay.yaml"):
        assert Config.from_yaml(path).jit_policy == "current", path


def test_the_prose_compiler_is_gone():
    """D-02: one compiler. An old config's `compiler: labels` still loads;
    asking for the removed arm is an error, not a silent fallback."""
    assert not hasattr(Config(name="t", llm_backend="mock", model="m", compiler="labels"), "compiler")
    try:
        Config(name="t", llm_backend="mock", model="m", compiler="prose")
        raise AssertionError("the removed arm is refused")
    except ValueError as exc:
        assert "prose compiler was removed" in str(exc)


async def test_deadline_admission_defers_a_line_it_cannot_answer_in_time():
    """RUN-06's candidate (X-09 only; RUN-06 rejected, the flag kept): a
    line 0.3 s before a fire cannot be answered before that fire, so it is
    answered from the clip after it; the schedule's estimates are the
    rolling p95 floored by the baseline. Run on session A's record, the one
    X-09 measured against."""
    from unittest import mock
    from src.eval import runner
    import tests.jit_sim as jit_sim
    session_a = jit_sim.BASELINE_PATH.with_name("2026-09-18-session-a.json")
    with mock.patch.object(runner, "BASELINE_PATH", session_a), mock.patch.object(jit_sim, "BASELINE_PATH", session_a):
        await _deadline_case()


async def _deadline_case():
    sim = await simulate(clips=16, lines=[Line("the light turns warm", after_clip=8, before_fire=0.3)],
                         policy="deadline", buffer_seconds=1.5)
    deferred = sim.of("line_deferred")
    assert deferred and deferred[0]["needs_s"] > deferred[0]["left_s"], deferred
    policy = sim.of("jit_policy")
    assert policy and policy[0]["floors"]["render"] > 0 and policy[0]["tau_s"] > 0
    assert all(line.id is not None for line in sim.lines)
    assert sim.measured["film"]["lines_landed"] == 1
