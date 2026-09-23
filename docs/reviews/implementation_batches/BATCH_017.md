# BATCH_017 — Local experiments X-07, X-08, X-09, and the phase-1 readings (ARCH-01, ARCH-10, ARCH-11, ARCH-16)

**Status:** OWNER_REVIEW_REQUIRED (X-07, X-08, X-09 RESULT_READY) · readings: ARCH-10, ARCH-11, ARCH-16 COMPLETED; ARCH-01 BLOCKED on its validation · **Date:** 2026-09-23 · **Base:** `2d06358`

## Plan

**Why this batch.** Every approved row with code scope is implemented. Phase 3's local experiments are the next permitted work: X-07 and X-08 (LLM calls only, headless; prerequisites ARCH-02, ARCH-03 and RUN-05 done) and X-09 (local; prerequisites RUN-05, the JIT simulation, ARCH-06 done). The production arms of X-07/X-08 are also the phase-1 gate's "one headless run with scripted lines", which gives the pending readings of ARCH-01, ARCH-10, ARCH-11 and ARCH-16. Nothing gated is enabled: the arms are evaluation-only patches (X-07/X-08) or an off-by-default flag (X-09's `jit_policy: "deadline"`, which the record permits as experiment infrastructure for RUN-06).

**Budget.** No GPU. LLM: 14 runs × about 20 Director calls on gpt-4.1's priority tier plus Showrunner calls, and about 330 judge calls.

## Result

### Files

| File | Change |
|---|---|
| `scripts/experiments/story_authority.py` | New. X-07/X-08 driver: the real live loop headless in real time, a scripted viewer, four evaluation-only arms (`base`, `momentum_off`, `memory_only`, `hide_candidates`), two schedules (`directions`, `silence`), repetitions; every Director call recorded to `plans.jsonl`. |
| `scripts/experiments/judge_authority.py` | New. The LLM judge (gpt-4.1, temperature 0, seed 7, strict JSON) and the metrics (returns to the card, direction held, movement after the last line, REQUIRED notes, stagnation replans, Director latency, clip counts), aggregated per arm and schedule. |
| `scripts/experiments/admission.py` | New. X-09 driver: Monte Carlo over `tests/jit_sim.py`, four arms, adversarial lines, a 5 % doubled-call tail; summary. |
| `src/eval/runner.py`, `src/models/config.py` | `jit_policy: "current"` (production) or `"deadline"` (RUN-06's candidate, X-09 only): rolling-p95 estimates floored by the baseline, deadline admission (`line_deferred` in the timing log). Off by default; the config comment says it is not to be enabled in production. |
| `tests/jit_sim.py` | `policy`, `buffer_seconds`, `director_retry_rate`; the new timing fields are scaled back. |
| `tests/test_experiment_arms.py` | New: the arms are off in every shipped config; deadline admission defers a line it cannot answer in time (and the policy's floors are logged). (It also covers BATCH_018's prose compiler.) |
| `docs/reviews/experiments/X-07-X-08.md`, `docs/reviews/experiments/X-09.md` | The reports. |
| `docs/reviews/08_DECISION_UPDATE_PROPOSALS.md` | New: the results and proposed dispositions for the owner. |

### Results in one line each

- **X-07:** momentum holds a direction about 10 s longer (noisy); a card landing clears it 18-48 s after the line in 5 of 6 clearings, and the returns to the card follow. Proposal: keep momentum with the record's reset-scope fix and a 90-s expiry.
- **X-08:** memory-only moves least when silent (0.38 against 1.15 changes per minute; a 151-s static stretch); hiding candidates while a direction is active holds directions longest and removes most card-following during a direction, but needs the expiry. Proposal: keep the planning half; E5 with ARCH-08's expiry.
- **X-09:** no arm meets all criteria; the 3.5-s candidate removes stalls at +2.7 s of median steering latency; the stalls come from beat-end Director calls. Proposal: reject RUN-06 as specified.

### Readings for the implemented rows (production `base` runs)

| Row | Validation | Reading | State |
|---|---|---|---|
| ARCH-11 | Director latency on answer calls | 13 of 13 answer beats ≤ 2 clips; answer calls 4.22 s mean (max 6.05 s), others 3.51 s; output size equal to session A's; the expected 0.3-0.5 s is not visible against the day's variation | COMPLETED (the reading is taken; the latency benefit is unconfirmed and recorded as such) |
| ARCH-10 | beat-length distribution | 91 one-clip, 224 two-clip, 15 three-clip, 1 four-clip beats; mean 10.3 s, inside the prompt's 6-24 s | COMPLETED |
| ARCH-16 | stale threads receive an explicit continue/resolve/abandon decision | the Showrunner names a decision per open thread in its rationale | COMPLETED |
| ARCH-01 | no beat contradicts an active viewer direction because of a card | **fails**: 7 card beats during an active direction in five runs (8 more after a clearing) | BLOCKED: expected to pass with ARCH-08/ARCH-09 as proposed (E5 run: 1 in three runs) |

### Validation of the infrastructure

- `python -m tests.run`: **111 passed, 0 failed** (107 before; `test_experiment_arms.py` adds 4, two of which cover BATCH_018's prose compiler).
- The drivers were smoke-tested on the mock backend (all four arms) before the real runs.

### Commit message

```
Local experiments X-07, X-08, X-09: story authority and line admission

Drivers and a judge for the owner's story-authority questions (real
Director, headless, scripted lines, evaluation-only arms) and a Monte
Carlo over the JIT simulation for line admission; RUN-06's candidate
policy is an off-by-default experiment flag. Results and proposed
dispositions for the owner are in 08_DECISION_UPDATE_PROPOSALS.md:
keep viewer momentum with a scoped reset and a 90 s expiry, keep the
Showrunner's planning half and hide candidate beats while a direction
is active, and do not adopt deadline admission as specified. The
production runs give the phase-1 readings: answer beats stay within two
clips, beats run 6-18 s, the Showrunner decides on every stale thread,
and the authority rule alone does not stop a card from pulling the film
away from a viewer's direction.
```
