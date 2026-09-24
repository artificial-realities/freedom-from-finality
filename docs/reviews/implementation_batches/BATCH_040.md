# BATCH_040 — Close-out: RUN-15 kept, ARCH-12 deferred, the working tree cleaned

**Status:** COMPLETED · **Date:** 2026-09-24 · **Base:** `8ef791d`

## Plan and result

The owner accepted both open recommendations:
- **D-18:** RUN-15 stays on (`plan_early: true`) → `COMPLETED`.
- **D-19:** ARCH-12 is deferred after X-19 → `NOT_APPLICABLE`, still `EXPERIMENT_REQUIRED` in the record.

Every decision row in the ledger is now `COMPLETED` or `NOT_APPLICABLE`. The one open item is X-26's GPU half, which needs a reference image for `tests/fixtures/lighthouse`.

The owner also asked to delete the unused files and the untracked ones no longer needed:

| File | What happened | Why |
|---|---|---|
| `stories/lego-void/legoman.png` | Deleted (the committed square and the owner's master in the working tree) | Unused since BATCH_037: the story's reference is `legoman-16x9.png`. A copy of the master stays in `logs/experiments/gpu3/x12/legoman-master.png` (git-ignored, local only). `tests/test_i2v.py` and `tests/test_shape.py` no longer open it: the letterbox tests draw a square reference, and the render tests send the story's own. |
| `docs/reviews/01`-`04_*_review.md` | Committed | The evidence the decision record cites row by row ("03 §5", "04 §7.4"), as do `CLAUDE.md` ("review 03 §2.2") and `benchmarks/runtime/2026-09-18-session-a.json`. |
| `PROJECT_SYSTEM_AUDIT.md` → `docs/reviews/` | Committed | The input the reviews and the record name (the record cites it by file name). |
| `08_FABLE_AUTONOMOUS_WORK_LOOP.md` → `docs/reviews/` | Committed | The rules the ledger's states and batches follow; a later decision can restart the loop from it. |
| `docs/reviews/05_consolidated_decision_record.md` | The owner's FINAL edit committed unchanged | Its SHA-256 still equals the one the ledger recorded at creation (`1d2674…`). |

`CLAUDE.md` said the Lego story stays continuous "until its GPU render check"; the check ran, and it now cites D-16.

## Validation

- `python -m tests.run`: 145 passed, run on the staged tree without the deleted image.

**Commit:** this batch's.
