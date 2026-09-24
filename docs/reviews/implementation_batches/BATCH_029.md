# BATCH_029 — A fire-aware scripted viewer for X-10; the tests stop depending on the story's image

**Status:** COMPLETED (preparation for GPU session 3, D-09) · **Date:** 2026-09-24 · **Base:** `b4c4e08`

## Plan

X-10 (the 300-clip run) checks all RUN-04 gates over a long film, and RUN-04's protocol wants about a third of the lines within 1 s of a render's fire. GPU session 2 showed a fixed offset into a segment cannot find the fire (it moves between +1.3 and +4.5 s with the beat; 1 of 9 lines). The loop now announces each fire in the timing log (RUN-05's log; observability only), and the scripted viewer can time a line against it.

## Result

| File | Change |
|---|---|
| `src/eval/runner.py` | `fire_due` (turn, `in_s`: seconds until the fire, from the record's wall time), written once per segment and again only if the moment moves by more than 0.2 s (`FIRE_DUE_STEP_S`) |
| `scripts/steer.py` | `SEG@SECONDS:text` with `--timing <run's timing.jsonl or glob>`: once SEG plays, at the next announced fire plus SECONDS (negative: before it); the target fire is chosen once, with room to send before it |
| `tests/test_jit.py`, `tests/jit_sim.py` | the last announcement of every steady clip names its fire within 0.3 s; `in_s` scaled with the simulation |
| `tests/test_i2v.py` | the two reference-fit tests use a synthetic 380 × 381 square instead of `stories/lego-void/legoman.png`, which the owner replaced with the 16:9 master for X-12 (uncommitted, theirs; the square original is kept for X-12 as `logs/experiments/x12/legoman-square.png`) |

## Validation

- `python -m tests.run`: 133 passed (with the owner's master in the working tree).
- `test_jit` 3 of 3 repeats.
- A replay film on the web player (mock Director, replayed 153-frame clips, 4-s render delay) with `steer.py --timing`: a line aimed at 0.3 s before a fire arrived 0.22 s after it (the announced fire moved earlier after the target was chosen), one aimed at 0.3 s after arrived 0.32 s after; both within RUN-04's 1 s.

## Notes

The owner's master (1672 × 941, 2.3 % off the render aspect: cover-resized, no letterbox) is in the working tree as `stories/lego-void/legoman.png`. Session drivers ship `git archive HEAD`, so X-10 renders from the committed square until X-12 decides the asset swap (RUN-11).

**Commit:** this batch's.
