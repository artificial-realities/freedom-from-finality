# BATCH_039 — X-19: first-clip streaming (gates ARCH-12)

**Status:** OWNER_REVIEW_REQUIRED (X-19 and ARCH-12 RESULT_READY) · **Date:** 2026-09-24 · **Base:** `ee2cf19`

## Plan and result

The owner asked whether everything in the decision record had been done. The audit (BATCH_038) found X-19 outstanding: phase 3 of the record lists it, and ARCH-12 is `EXPERIMENT_REQUIRED` behind it. It needs OpenAI calls only.

| File | Change |
|---|---|
| `scripts/experiments/x19_streaming.py` | Evaluation only: the answer call as production makes it, and the same messages streamed against a copy of `BeatPlan` with `clips` after the first clip's context fields; an incremental scanner reports the first clip's object as it closes. |
| `tests/test_experiment_arms.py` | The scanner under arbitrary chunking and with braces, brackets, quotes and a `"clips"` key inside strings; the copy has exactly `BeatPlan`'s fields. |
| `docs/reviews/experiments/X-19.md` | The report. |

Nothing in production changed.

## Result

The streamed first clip closes at 1.99 s median, against 2.66 s for today's whole answer call. That saves 0.6-0.9 s per answer, and every streamed reply is valid (50 of 50). Proposal: defer ARCH-12 (`08_DECISION_UPDATE_PROPOSALS.md`).

## Validation

- `python -m tests.run`: 145 passed (1 new).

**Commit:** this batch's.
