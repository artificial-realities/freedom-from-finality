# BATCH_028 — `delivered_request` left at 0 on a beat that answers its own line (ARCH-07)

**Status:** COMPLETED · **Date:** 2026-09-24 · **Base:** `55a0602`

## Plan and result

GPU session 2's live transcript showed the Director setting `delivered_request` to its own line's segment on every absorbed answer (9 of 9); the validator ignored it with the warning "delivered_request N names no deferred request". ARCH-07 (APPROVED; scope `schemas.py`, `validator.py`, `director.system.md`) owns the field, so the fix is inside it: `src/prompts/director.system.md` now says that a beat answering this beat's own viewer input leaves the field 0 (`intervention` records that), and that it is always 0 with no requests owed. No code change.

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | 133 passed |
| One real-Director run on X-07/X-08's harness (`logs/experiments/story-authority-b028/`, directions schedule, 40 segments) | 3 of 3 answer beats absorbed with `delivered_request` 0; the warning 0 times (the live run: 9) |

**Commit:** this batch's.
