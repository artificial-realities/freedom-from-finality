# BATCH_016 — Drain before emit, and fail-soft rendering with rollback (RUN-07, RUN-09)

**Status:** COMPLETED · **Date:** 2026-09-23 · **Base:** `99607e1`

## Plan

**Why this batch.** RUN-07 (phase 1) and RUN-09 (phase 5, which depends only on RUN-07) are one mechanism in one loop: knowing a render's outcome before the next clip is emitted is what makes a rollback possible. One boundary: the live loop with a failing replay backend, the JIT simulation, the suite. Explicitly approved in Q3 (the owner's answer is the spec: retry once only when buffer time remains; otherwise restore world and beat state to before the failed clip and replan from the last clip the viewer received; never end the film on one failure; log every failure and recovery; prevent retry loops).

**Decision IDs.**
- **RUN-07** — `emit(k+1)` after render k is known to have succeeded.
- **RUN-09** — Retry once if `now + R_p95 + guard ≤ air(k)`; otherwise restore the world from a snapshot taken before `apply_clip(k)`, cut the beat, tell the Director the picture ends on k−1; an exception in `emit` takes the same route. Rollback: a flag.

**Risks / rollback.** A rollback after the next beat was already planned discards that plan (its viewer line is carried to the replan) and must not lose a Showrunner update that landed in between (it is re-applied). A Showrunner run spawned at the failed clip keeps its snapshot (rare; noted). Rollback of the batch: `fail_soft: false`, or revert.

**Validation required.** RUN-07: JIT simulation. RUN-09: a failed render rolls the world back and the next prompt continues from k−1. Suite.

**Baseline.** 100 passed at `99607e1`.

## Result

### Files changed

| File | Change |
|---|---|
| `src/eval/runner.py` | RUN-07: the producer drains the previous render right before `emit`, not before the fire. RUN-09: `_bg_render_and_enqueue` returns whether the clip was queued and retries once when the p95 of the last 20 renders plus `JIT_MARGIN_S` fits before the clip is due (always while the lead builds); a failed segment is rolled back (`graph.rollback_clip`), its history entry removed, the silence count restored, the lines that landed on it (and those of a beat planned since) re-queued, the viewer text carried into the replan, and the segment replanned under the same number; an `EmitFailed` is replanned the same way; `MAX_CONSECUTIVE_FAILURES` (3) stops the film; timing records `render_done.attempt`, `render_retry`, `rollback`, `line_requeued`, `emit_failed`, `render_skipped` (flag off). |
| `src/graph/director_engine.py` | `emit` snapshots the engine and world before the clip (`_snapshot`/`_restore`), keeps the Showrunner updates landed since, and on an exception restores, cuts and raises `EmitFailed`; `rollback_clip(segment, reason)` restores, re-applies those updates, drops the pending interventions it will answer again, cuts the beat with a "never reached the viewer … the picture ends on …" note for the next plan, rewrites `events.jsonl`, and returns the text to answer again. |
| `src/story/world.py` | `save(rewrite=True)`. |
| `src/models/config.py` | `fail_soft: true`. |
| `src/util/story_log.py` | `log_note` for the transcript. |
| `tests/test_fail_soft.py` | New, 7 tests. |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | Failures paragraph and constraint. |

### Validation

| Check | Command | Result |
|---|---|---|
| RUN-07 | `test_the_previous_render_is_known_before_the_next_clip_is_emitted`; `tests/test_jit.py` (the live config's schedule, gates, no stall) | pass |
| RUN-09 (the row's validation) | `test_a_failed_render_rolls_the_world_back_and_the_next_clip_continues_from_the_one_before` | pass: render 3 fails with no time to retry → one `rollback`; the next Director prompt says clip 1 of the beat never reached the viewer and "The picture ends on: Lego Man standing still" (clip 2's end state); segment 3 is emitted again from a new beat; the event log, history and played turns are 1-5 once each |
| Retry | `test_a_render_is_retried_once_when_there_is_time` | pass: a failure while the lead builds is retried and the film is unchanged |
| Emit exception | `test_an_emit_exception_is_rolled_back_instead_of_ending_the_film` | pass |
| No retry loop | `test_repeated_failures_stop_the_film_instead_of_looping` | pass: a dead renderer stops the film after three segments with an `error` record |
| Rollback flag | `test_without_fail_soft_a_failed_render_is_skipped` | pass: the old skip |
| A line on a failed clip | `test_a_line_whose_answer_never_rendered_is_answered_again` | pass: re-queued, recorded once, answered by the replanned segment 3 |
| Full suite | `python -m tests.run` | **107 passed, 0 failed** (100 before) |
| pyflakes | `src`, the new test | clean apart from the pre-existing `runner.py` finding |

### Deviations

- The replanned clip reuses the failed segment's number, so the viewer's segment numbers stay contiguous; the timing log shows the first `emit`/`fire` and the `rollback` before the second.
- "R_p95" is a rolling p95 of the last 20 render walls used only for the retry decision; the schedule's own estimate stays as it is (quantile estimates for scheduling are RUN-06, experiment-gated).

### Next batch

A headless run with the real Director and scripted lines for the pending readings (ARCH-01, ARCH-16, ARCH-11, ARCH-10), then the experiment preparations (LTX-01's comparison compiler behind a flag; X-07, X-08, X-09 locally).

### Commit message

```
The next clip waits for the last render's outcome; a failed render is retried or rolled back

RUN-07: the producer drains the previous render right before emitting
the next clip (in steady state it lands about 1.2 s before the fire,
so nothing waits). RUN-09: a failed render is retried once when the
retry can still land before its clip is due; otherwise the world and
the beat are restored from a snapshot taken before the clip was applied
(Showrunner updates landed since are applied again), the beat is cut,
the next plan is told the picture ends on the clip before, a viewer
line whose answer was on that clip is answered again, and the segment
is replanned. An exception while emitting takes the same route; three
failed segments in a row stop the film; fail_soft: false restores the
old skip.
```
