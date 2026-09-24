# BATCH_038 — Owner decisions D-16, D-17; ledger reconciled with D-08 and D-10

**Status:** COMPLETED (records only) · **Date:** 2026-09-24 · **Base:** `e30a7a5`

## Plan and result

The owner answered GPU session 4's questions: D-16, the Lego story stays one continuous take (cuts remain a story option); D-17, the floor drift over long films is left for now. Both are recorded in `08_OWNER_DECISIONS.md`.

The owner then asked whether the whole decision record had been worked through. An audit of the ledger against the record found:

| Row | Was | Now | Why |
|---|---|---|---|
| LTX-15 | RESULT_READY | COMPLETED | D-16: built as a story option, off for Lego; nothing further. |
| AUD-01 | NOT_STARTED | NOT_APPLICABLE | No code scope of its own; realised through AUD-04 and AUD-06; its validation experiments were skipped (D-08, D-10). |
| AUD-05 | BLOCKED | NOT_APPLICABLE | D-08 skipped X-01; the row stays gated by the owner's choice, not by a blocker. |
| AUD-07 | NOT_STARTED | NOT_APPLICABLE | D-10 skipped X-15 and X-29. |
| RUN-02 | NOT_STARTED | NOT_APPLICABLE | D-10 skipped X-18. |

**Still open after this batch:**
- **RUN-15** waits for the owner's review of its result (`plan_early` is on).
- **ARCH-12** needs X-19. The record's phase 3 lists X-19 among the experiments to run, and its exit criterion gives every `EXPERIMENT_REQUIRED` row a result. X-19 measures on the LLM side (time to clip 1, schema validity over 50 answer calls), so it needs no GPU. It is the next batch.
- **X-26's GPU half** needs a reference image for `tests/fixtures/lighthouse`.
- **X-14** runs only if AUD-09 is reopened.
- **X-16, X-17, X-21, X-24, X-27, X-30** are optional and later in record §4.3. X-21 was not triggered, and X-27 depends on X-18, which was skipped.

## Validation

Records only; no code changed.

**Commit:** this batch's.
