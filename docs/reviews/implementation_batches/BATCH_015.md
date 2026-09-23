# BATCH_015 — On top of the shape: an explicit buffer, beats in seconds, renderer rules (RUN-03, ARCH-10, LTX-04)

**Status:** COMPLETED (RUN-03, LTX-04 implemented; ARCH-10's beat-length reading and X-22 pending a run) · **Date:** 2026-09-23 · **Base:** `8574087`

## Plan

**Why this batch.** The three approved rows that depend only on RUN-01 (done in BATCH_014): the scheduler's steady-state buffer (RUN-03) and the Director's guidance in the shape's seconds (ARCH-10) with the renderer rules (LTX-04). Two files carry most of it (`runner.py`/`config.py` and `director.system.md`); one boundary (the JIT simulation, prompt-content tests, the suite).

**Decision IDs.**
- **RUN-03** — Rename `lead_clips` to `startup_clips`; add `buffer_seconds`, the steady-state target the schedule keeps (4.7 s implicit today). Correct CLAUDE.md and the audit ("N+3 with lead 1").
- **ARCH-10** — Keep `max_clips_per_beat: 4`; phrase the prompt guidance as time from the selected shape and the four-clip cap.
- **LTX-04** — Three renderer rules in `director.system.md` (one plausible movement per clip, two joined by "a moment later"; no internal states without a visible cue; about a clip's duration of action), and `camera_motion` as a verb phrase. No schema change. X-22 is post-implementation validation.

**Risks / rollback.** RUN-03 changes the fire formula from `1.25 × R̂ + 0.5 + slack` to `R̂ + buffer_seconds`; at the baseline's 4.8 s render they are equal (4.7 s), and the Director's late start keeps its safety factor. Prompt rows change the Director's output (unmeasured until a run). Rollback: revert.

**Validation required.** RUN-03: the JIT simulation test. ARCH-10: the beat-length distribution in the timing log (needs the real Director). LTX-04: X-22 (GPU, post-implementation).

**Baseline.** 98 passed at `8574087`.

## Result

### Files changed

| File | Change |
|---|---|
| `src/models/config.py` | `startup_clips` (was `lead_clips`) and `buffer_seconds: 4.7` (replaces `render_slack_seconds`); a validator maps an old config once (`lead_clips` → `startup_clips`; `render_slack_seconds` → `buffer_seconds` = slack + 1.7 s, what the old formula kept at the baseline's render, `LEGACY_SLACK_TO_BUFFER`). |
| `src/eval/runner.py` | A render fires `R̂ + buffer_seconds` before its clip is due; `JIT_SAFETY`/`JIT_MARGIN_S` remain for the Director's late start only; the timing log's `session_start` records `startup_clips` and `buffer_seconds`. |
| `configs/*.yaml` | `startup_clips`, `buffer_seconds: 4.7`. |
| `src/prompts/director.system.md`, `src/agents/director.py` | "a beat is 6 to 24 seconds of film … a quick reaction is one clip (6 seconds), most beats run 12 to 18 seconds, a long held state may take all 24", all from the shape and the cap; a "Writing for the video model" section with the three rules; `camera_motion` as a verb phrase with examples. The system prompt stays static within a run. |
| `tests/jit_sim.py`, `tests/test_jit.py` | The simulation scales `buffer_seconds`; the steady-buffer assertion is `buffer_seconds` itself. |
| `tests/test_live_timing.py` | Its scenario (the render of clip 3 in flight when the line arrives) now states a 1 s buffer instead of relying on the old formula's implicit 0.575 s at zero slack. |
| `tests/test_shape.py` | Two new tests (below). Test configs drop the ignored `min_pause_seconds`. |
| `CLAUDE.md`, `docs/ARCHITECTURE.md` | `startup_clips`, `buffer_seconds`. |

### Validation

| Check | Command | Result |
|---|---|---|
| RUN-03 | `tests/test_jit.py::test_the_live_schedule_holds_the_picture_under_adversarial_lines` (live config at 25×, baseline draws, adversarial lines); `test_the_buffer_is_explicit_and_startup_is_startup` | pass: every steady-state margin within 0.3 s of `buffer_seconds` (4.7 s), all timing gates pass, no stall; the renames and the one-time mapping |
| ARCH-10 / LTX-04 prompt content | `test_the_director_is_told_lengths_in_seconds_and_how_the_renderer_reads` | pass: 6 s shape → "6 to 24 seconds", "12 to 18"; a 4 s shape → "4 to 16" (from the shape); the three rules and the verb-phrase camera in the system prompt; the compiler's clause already reads verb phrases (`test_compiler_camera_clause`, BATCH_007) |
| Full suite | `python -m tests.run` | **100 passed, 0 failed** (98 before) |
| pyflakes | `src`, changed tests | clean apart from the pre-existing `runner.py` finding (an unused import in `test_jit.py` removed) |
| ARCH-10 row validation (beat-length distribution) | — | **pending** the next run with the real Director |
| LTX-04 / X-22 | — | **not run** (post-implementation, GPU) |

### Deviations

- RUN-03's audit correction ("N+3 with lead 1") is not applied: `PROJECT_SYSTEM_AUDIT.md` is the owner's untracked file, which this loop does not edit (as with CLEAN-10). The corrected statement is in CLAUDE.md and ARCHITECTURE.md.
- `render_slack_seconds` is folded into `buffer_seconds` rather than kept beside it: with an explicit buffer the slack has no separate meaning. X-09's arms "slack 3.0 / 1.0 / 0.5" correspond to buffers 4.7 / 2.7 / 2.2 at the baseline's render; RUN-06 (experiment-gated) keeps its rolling-p95 estimate and the lower buffer for after X-09.

### Next batch

RUN-07 (drain the previous render before emitting the next clip) with RUN-09 (fail-soft render and emit), then a headless run with the real Director and scripted lines for the pending transcript and latency readings (ARCH-01, ARCH-16, ARCH-11, ARCH-10), then the experiment preparations.

### Commit message

```
The schedule keeps an explicit buffer; the Director is told beats in seconds and how the renderer reads

RUN-03: lead_clips becomes startup_clips (the startup gate and queue
size only) and the steady state is buffer_seconds, 4.7 s, what the
schedule kept implicitly: a render fires its estimate plus the buffer
before its clip is due. Old configs map onto both. ARCH-10: the beat
guidance is time from the clip shape and the four-clip cap (6 to 24 s,
most beats 12 to 18). LTX-04: the Director prompt carries three generic
renderer rules (one plausible movement per clip, no inner state without
a visible cue, about a clip's worth of action) and asks for the camera
move as a verb phrase.
```
