# BATCH_011 — The viewer path: a second line joined, deferred requests by identity, answers capped (ARCH-06, ARCH-07, ARCH-11)

**Status:** COMPLETED (ARCH-06, ARCH-07); ARCH-11 implemented, its latency reading pending a real-Director run · **Date:** 2026-09-23 · **Base:** `5b0f0b2`

## Plan

**Why this batch.** The three phase-1 rows on the viewer's side of the Director contract: how lines reach a plan (ARCH-06, `runner._listen`), how a deferred request is resolved (ARCH-07, schema + validator + system prompt), and how long an answer is (ARCH-11, user-prompt gate). One contract, one boundary: the validator and context tests, the JIT simulation, the suite.

**Decision IDs.**
- **ARCH-06** — A line arriving between the Director's return and the fire moment, while the answered beat has not played, is treated as the cancel path treats one: the pending intervention recorded by the first plan is dropped, the texts are joined, one plan answers both. RUN-06 (experiment-gated) touches the same branch later.
- **ARCH-07** — `BeatPlan.delivered_request: int` (the segment of the deferred request the beat delivers; 0 = none), strict-subset; the validator resolves that item. Explicitly approved in Q6 ("the project's existing stable intervention identifier": the segment, which the viewer section already prints and which is unique per plan).
- **ARCH-11** — The cap on answer beats in the user prompt's gates when a line is present; the system prompt is untouched (cached).

**Files.** `src/eval/runner.py`, `src/graph/director_engine.py`, `src/story/schemas.py`, `src/story/validator.py`, `src/story/context.py`, `src/agents/director.py`, `src/models/config.py`, `src/prompts/director.system.md`, `src/llm/mock_backend.py`, `tests/jit_sim.py`, `tests/test_jit.py`, `tests/test_story.py`, docs.

**Risks / rollback.** ARCH-06 is timing-dependent (covered by the JIT simulation). ARCH-07 adds a field the Director must fill; a missing or wrong value leaves the request owed (with a warning), never resolves the wrong one. ARCH-11 shortens answers; the next beat continues. Rollback: revert the commit.

**Validation required.** ARCH-06: two lines inside the window → one plan with both texts. ARCH-07: validator test with two deferred requests. ARCH-11: Director latency on answer calls (timing log; needs the real Director). Suite.

**Baseline.** 77 passed at `5b0f0b2`.

## Result

### Files changed

| File | Change |
|---|---|
| `src/eval/runner.py` | `_answer(text, turn)` replaces the two copies of "cut the beat, plan from the line" in `_listen`. When `graph.beat_unplayed()` and the last plan answered a line (`planned_text`), it drops that plan's pending intervention (`discard_pending_input`), moves the earlier line ids from `beat_lines` back into `plan_lines` (so the timing log shows them landing, not dropped), joins the texts and plans once. |
| `src/graph/director_engine.py` | `beat_unplayed()`; the beat is truncated to `director.clip_cap(config, line)`. |
| `src/story/schemas.py` | `BeatPlan.delivered_request: int = 0`. |
| `src/story/validator.py` | `_owed_item(world, segment)`; `apply_clip` resolves the named request as absorbed; without the field an absorbed/declined beat with no new line resolves the only request owed and, with two or more owed, none (warning) — the old `deferred[-1]` guess is gone; `check` warns on a segment that names nothing. |
| `src/prompts/director.system.md` | The `delivered_request` field. |
| `src/models/config.py` | `answer_max_clips: 2`. |
| `src/story/context.py`, `src/agents/director.py` | `format_gates(answer_clips=…)` adds "This beat answers the viewer: at most 2 clips. Start the answer now; the next beat continues it." when a line is present; `clip_cap()` is the single rule (never above `max_clips_per_beat`), used by `check` and the engine. |
| `src/llm/mock_backend.py` | The scripted deferred delivery names its request (`delivered_request`) instead of setting `intervention: absorbed`. |
| `tests/jit_sim.py` | `Line.after_answer` (sent that many seconds after the call answering the previous line returns); `Sim.world`. |
| `tests/test_jit.py`, `tests/test_story.py` | Three new tests (below); one assertion updated to the new applied-op text. |

### Validation

| Check | Command | Result |
|---|---|---|
| ARCH-06 | `tests/test_jit.py::test_a_second_line_after_the_answer_is_joined` (live config at 25×, line 1 0.3 s after a fire, line 2 0.4 s after the Director's answer returns) | pass: one intervention "A soccer ball appears at your feet. Make it bounce." absorbed; both line ids land on one clip; no `line_dropped`; two answering calls (the first answer, then one for the pair); no stall. **Against the old runner the test fails**: two interventions, the first one given the second beat's disposition |
| ARCH-07 | `tests/test_story.py::test_a_deferred_request_is_resolved_by_identity` | pass: two owed; an absorbed beat naming none resolves neither (warning); an unknown segment is ignored with a warning; `delivered_request=2` resolves exactly the kick; a new line and a delivery in one beat both resolve |
| ARCH-11 | `tests/test_story.py::test_an_answer_beat_is_capped_and_told_so` | pass: cap 2 with a line, 4 without, never above the beat cap; the gate sentence is present only with a line; `check` warns past the cap |
| ARCH-11 row validation (Director latency on answer calls) | — | **pending**: needs the real Director; read from the timing log of the phase-1 run with scripted lines |
| Full suite | `python -m tests.run` | **80 passed, 0 failed** (77 before) |
| pyflakes | `src`, changed tests | clean apart from the pre-existing `runner.py` `nonlocal state` |

### Deviations

- ARCH-11's recommended choice states the cap in the gates; the engine also truncates an answer beat past it (as it already truncates past `max_clips_per_beat`), so the promise in the prompt is the behaviour.
- ARCH-07 keeps one narrow fallback (a single request owed, an absorbed beat without the field) so the Director's older habit does not leave an unambiguous request owed; the ambiguous case the row fixes no longer guesses.

### Next batch

The blueprint's narration and voice (AUD-06, LTX-05) with the audio path's single render path (AUD-04), then ARCH-19's cast policy.

### Commit message

```
A second line before the answer plays is joined; deferred requests are delivered by name

ARCH-06: a viewer line that arrives after the Director answered the
previous one but before that answer played is joined to it, as a line
that cancels a call in flight is: the earlier intervention is dropped
and the pair is planned, recorded and answered once. ARCH-07: BeatPlan
gains delivered_request, the segment of the deferred request a beat
delivers; the validator resolves exactly that one and no longer guesses
the last. ARCH-11: a beat that answers a line has at most two clips
(answer_max_clips), stated in the user prompt's gates and enforced.
```
