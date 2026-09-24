# BATCH_022 — Owner decisions recorded; LTX-01's caption-prose arm removed

**Status:** COMPLETED · **Date:** 2026-09-23 · **Base:** `32fd5fc`

## Plan

The owner answered the four open proposal groups in the session (the questions and answers verbatim in `docs/reviews/08_OWNER_DECISIONS.md`, D-01..D-04). The final decision record stays as signed; the owner's later decisions live in their own file and the ledger's status column shows `record → decision`. Of the decided rows, one needs code now with no dependency: **LTX-01**, rejected with the instruction to remove the arm ("one compiler; remove `src/story/compiler_prose.py` and its flag and tests"). The rest are either `REJECTED` with nothing to do (LTX-02, LTX-06, LTX-11, LTX-14, RUN-06, RUN-12) or `APPROVED` and planned as the next batches in roadmap order (ARCH-08 + ARCH-09; RUN-14; LTX-12 + AUD-02 + AUD-03).

Risk: none for production (the arm was off everywhere). Rollback: revert the commit.

## Result

| File | Change |
|---|---|
| `docs/reviews/08_OWNER_DECISIONS.md` (new) | D-01..D-04 as asked and answered; the new row RUN-14; what was not decided (AUD-09, X-01/X-13 listening, the deadline flag) |
| `src/story/compiler_prose.py` | deleted |
| `src/models/config.py` | `compiler` removed; `_one_compiler` drops an old `compiler: labels` and refuses any other value with a message |
| `src/graph/director_engine.py` | the flag check and the arm's import gone; `compile_clip` only |
| `tests/test_experiment_arms.py` | the two prose tests removed; `test_the_prose_compiler_is_gone`; the deadline arm's test stays |
| `scripts/experiments/gpu_session1.py` | the arm imported lazily in `prepare` only (with a note that it needs the file from git `32fd5fc`); `run` and `analyse` work without it |
| `scripts/replay_compiler.py` | the docstring's example no longer names the arm |
| `docs/ARCHITECTURE.md` | one compiler; what X-05 and X-06 found |
| `docs/reviews/08_IMPLEMENTATION_PROGRESS.md` | the decisions, the new row, X-21 not triggered |

`scripts/experiments/x06_forms.json` stays as X-06's recorded input.

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | 113 passed (114 − 2 prose tests + 1) |
| Session A's replay through the compiler (`logs/batch-022/session-a.prompts.json`) against today's earlier output (`logs/experiments/gpu1/probe-prompts.json`) | 64 of 64 prompts identical |
| `py_compile` on the engine, config and runner | pass |

## Next

ARCH-08 + ARCH-09 (story authority), validated with the real Director on X-07/X-08's scripted lines; ARCH-01's transcript check re-run on those runs.

**Commit:** this batch's.
