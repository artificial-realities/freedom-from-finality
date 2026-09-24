# BATCH_037 — GPU session 4: the standard size and the faster Director adopted; the cut checked

**Status:** COMPLETED (RUN-16, RUN-17) · RESULT_READY (LTX-15) · **Date:** 2026-09-24 · **Base:** `ca109e0` · **GPU:** ≈ $2.3 (≈ $12.6 of $30)

## Plan and result

GPU session 4 (`logs/batch-037/session4.sh`, report `docs/reviews/experiments/session4.md`):
- RUN-17's size check: probes and session A's chain at 640×352 and 512×288.
- LTX-15's render check: a scripted film with three cuts and its no-cut control.
- Two 64-segment live films at 512×288 with the 16:9 reference fitted strictly and guided cuts, one with gpt-4.1 and one with gpt-5.4-mini.

Then the adoptions the checks allowed:

| File | Change |
|---|---|
| `server/server_defaults.sh`, `server/vast_run.sh`, `server/README.md` | 512×288 by default (RUN-17). |
| `configs/director.live.yaml` | `i2v_reference_fit: "strict"` (RUN-17); `director_model: "gpt-5.4-mini"`, `director_reasoning_effort: "none"` (RUN-16). |
| `configs/director.yaml`, `configs/director.replay.yaml` | The same Director, so headless and replayed runs match the live one. |
| `stories/lego-void/legoman-16x9.png`, `story.json` | The Lego story's reference: a 768×432 medium-shot crop of the owner's 16:9 master (the figure at 75 % of the height). The owner's `legoman.png` in the working tree is untouched; the committed square is no longer the story's. |
| `benchmarks/runtime/baseline.json`, `README.md` | The new baseline: film B (512×288, gpt-5.4-mini). Session 2's record is kept as `2026-09-24-session2.json`, and the README table is regenerated. |
| `tests/test_engine.py`, `tests/test_story.py` | They follow the story's seed image instead of naming `legoman.png`. |
| `scripts/experiments/session4_analyse.py` | Sheets, cut detection, render walls, the live films' cuts. |
| `tests/jit_sim.py` | The scripted viewer aims a line "N s before a fire" at the loop's own announcement of that fire (`fire_due`, as `scripts/steer.py` does), not at the median fire phase. The new baseline's render walls spread more (sd 0.14 s against 0.06 s), so the running render estimate moves each fire by up to a quarter second. The median then sent `test_jit`'s 0.3-s line only 0.07 s early, failing half the runs. `FIRE_DUE_STEP_S` is scaled like the other constants. |
| `CLAUDE.md` | The standard size, the strict reference, the Director. |

## Readings

- **RUN-17: met.** 512×288 renders at ratio p95 0.582 against 0.843 for 640×352 on the same box (−31 %). Identity, detail and framing hold by eye, and the background is cleaner with the 16:9 reference. The medium-shot reference opens without X-12's zoom. Adopted.
- **RUN-16: met live.** Film B (gpt-5.4-mini) passes every RUN-04 gate. Its lowest queue margin is 2.31 s, and it answers lines in 2.96 s mean (3.67 max). Film A (gpt-4.1) fails the margin gate (0.48 s) and answers in 4.68 s mean (6.48 max). Film B needed 31 beats against 47. Adopted.
- **LTX-15: controllable, costly for the Lego story.**
  - Written cuts in and out of close-ups happen (4 of 4), and none happened unwritten (0 in 14).
  - They land 2-4 s into the clip, and angle changes render as the figure turning.
  - Close-ups repaint the face, and live, 4 of 7 cuts in one film were dissolves and 3 cut clips degraded.
  - Kept as a story option; the Lego story stays one continuous take (proposal).
- **A finding for the owner:** every long film, session 2's included, drifts from the white void to a LEGO baseplate floor as props accumulate.

## Validation

- `python -m tests.run`: 144 passed (and `test_jit` 5 of 5 in a row after the harness fix).
- `scripts/bench_report.py` on both films; `scripts/seam_check.py` and `scripts/audio_check.py` on film B (the report has the numbers).

**Commit:** this batch's.
