# BATCH_033 — Plan the next beat earlier (RUN-15, D-14)

**Status:** RESULT_READY (built, on by default; the stall half of the validation holds only with a fast Director) · **Date:** 2026-09-24 · **Base:** `2e4df38`

## Plan and result

D-14 asked for the next beat's Director call to start earlier near a beat's end, so a slow call (X-10: 6-14 s) has more time before the next fire. The row's validation: the JIT simulation with the X-10 Director's tail, fewer stalls and unchanged steering.

| File | Change |
|---|---|
| `src/models/config.py` | `plan_early: bool = True` |
| `src/eval/runner.py` | `_listen`: when the beat has no clip left and `plan_early` is set, the Director call for the next beat starts at once (the last clip has just been emitted). The loop then waits for the fire with the beat in hand, still listening. A line during the call cancels and restarts it, as before; a line after it drops the unplayed beat and is answered, as before. `false` keeps the old start: one estimated call × `JIT_SAFETY` + `JIT_MARGIN_S` before the fire. |
| `tests/test_engine.py` | `test_the_next_beat_is_planned_as_soon_as_the_last_clip_is_out`: every beat-end call starts within 0.05 s of the previous clip's emit, and a line during clip 4 still lands at 4. The end-to-end schedule test pins `plan_early=False` (it asserts the old start). |
| `tests/jit_sim.py` | `plan_early` passes through. `Line.into_clip` sends a line at a fixed moment of the film (the same in any policy), for paired comparisons. `Samples.director_calls(timing.jsonl)` resamples a real run's completed calls. |
| `scripts/experiments/plan_early.py` | The comparison below. |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | The schedule. |

## Readings

**The scripted line was the first misreading.** A first run with the clipped-normal Director (`Spread`) and `mid_call` lines showed mid-call steering 1.2 s worse with early planning (11.73 → 12.90 s). `mid_call` sends the line halfway through the call. The early arm's call starts earlier, so its line is sent earlier in the film and lands on the same clip. Lines sent at the same moment of the film in both arms (`into_clip`) steer identically: X-10 regime, 72 paired lines, all within ±0.12 s.

**The clipped normal was the second.** Its floor (mean − 3 sd) reaches zero under X-10's spread (4.66 ± 1.62 s). Sub-second calls halve the running estimate, and the next normal call is then late. The real X-10's completed calls never go below 2.19 s: its 0.15-0.32-s calls are cancelled ones. The readings below resample real runs' completed calls (`Samples`), eight seeds of 60 clips per cell:

| Director (real run) | Film | Stalls, late → early | Held (s) | Lowest queue margin (s) | Paired steering, early − late | Calls cancelled |
|---|---|---|---|---|---|---|
| X-10 (172 calls, mean 4.67, p95 6.96, max 13.82) | quiet | 5 → 4 | 6.0 → 6.0 | −3.30 → −3.33 | — | 0 → 0 |
| X-10 | a line every 6 clips | 40 → 41 | 55.9 → 70.1 | −4.43 → −9.00 | mean −0.35 s; 2 worse, 5 better (> 0.5 s) | 17 → 20 |
| Session 2 (39 calls, mean 2.80, p95 4.03, max 5.35) | quiet | 0 → 0 | 0 → 0 | 2.42 → 4.43 | — | 0 → 0 |
| Session 2 | a line every 6 clips | 0 → 1 | 0 → 0.5 | 0.25 → −0.54 | mean −1.17 s; 0 worse, 14 better (a clip sooner) | 1 → 16 |

The committed driver's run (`logs/batch-033/plan_early.out`); an earlier run of the same logic agreed within one stall per cell (timing jitter at 25×).

With the clipped-normal fast Director (2.9 ± 0.9 s, 4 % re-asks), stalls went 4 → 2 and held time 6.5 → 1.5 s.

- **X-10's held time with lines** (55.9 → 70.1 s) is one seed (4.1 → 20.5 s in the earlier run). Draws are seeded per segment and attempt, and the early arm cancels more calls, so the two arms draw different call times after the first cancellation. In that seed the early arm drew a 13.9-s answer and then a 16.9-s plan (two draws: the mock's validator re-ask). Early planning started that plan right after the previous fire, which is the earliest it could start.
- **Why the lever cannot reach X-10's stalls.** At X-10's mean the old start is one estimated call × 1.25 + 0.5 s ≈ 6.3 s before the fire. That is about one 6-s clip, so it already fell at about the last clip's emit, which is where the early start puts it. Two of X-10's three real stalls were line answers, which neither schedule touches. The third, a 11.6-s beat-end call at turn 189, gains about 0.5 s of its 0.9 s.
- **With a fast Director** the old start waits (the estimate is below a clip), and planning at once keeps the queue fuller (lowest margin 2.42 → 4.43 s, quiet). Lines land a clip sooner when they arrive while the old schedule would still be waiting to plan.
- **Cost:** a line after the early plan returns discards that beat (session 2 with lines: 16 of ~310 calls). Tokens, not latency.
- The simulated line films stall far more than the real X-10 did (40 per 480 clips against 3 per 300). Lines here come at every phase of a clip, and under the current policy each is answered at once against the 4.7-s buffer. That is the line policy's (X-09), not this row's; not pursued here.

## Disposition

On by default: neutral with a slow Director, better with a fast one on every measure except one 0.5-s stall in 48 line-heavy minutes. RUN-15 is `RESULT_READY`, not `COMPLETED`: the row's "fewer stalls with the X-10 tail" is not met, because the old schedule already started X-10-speed calls at the last emit. X-10's stalls need the call itself shorter (RUN-16, next) or the first clip sooner (X-19/ARCH-12). Rollback: `plan_early: false`.

## Validation

- `python -m tests.run`: 138 passed, with `plan_early` on and the new test.
- `scripts/experiments/plan_early.py` produced the table (about 25 minutes; run alone, since the simulation at 25× is sensitive to a loaded machine).

**Commit:** this batch's.
