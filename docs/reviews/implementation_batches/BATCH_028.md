# BATCH_028 — `delivered_request` left at 0 on a beat that answers its own line (ARCH-07)

**Status:** COMPLETED · **Date:** 2026-09-24 · **Base:** `55a0602`

## Plan and result

GPU session 2's live transcript showed the Director setting `delivered_request` to its own line's segment on every absorbed answer (9 of 9); the validator ignored it with the warning "delivered_request N names no deferred request". ARCH-07 (APPROVED; scope `schemas.py`, `validator.py`, `director.system.md`) owns the field, so the fix is inside it: `src/prompts/director.system.md` now says that a beat answering this beat's own viewer input leaves the field 0 (`intervention` records that), and that it is always 0 with no requests owed. No code change.

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | at the prompt commit (`f85d7de`) 132 passed, 1 failed: `test_jit`'s adversarial scenario, flaky since the new baseline (not this change; recorded here because the report first said 133). After the test fix below: 133 passed |
| One real-Director run on X-07/X-08's harness (`logs/experiments/story-authority-b028/`, directions schedule, 40 segments) | 3 of 3 answer beats absorbed with `delivered_request` 0; the warning 0 times (the live run: 9) |

## The JIT test's flakiness (fixed in this batch)

With GPU session 2's baseline (renders 4.02 ± 0.06 s) `test_jit.test_the_live_schedule_holds_the_picture_under_adversarial_lines` failed about one run in four on a quiet machine, on a different check each time. The failing checks tested the harness's own placement, not the scheduler: the harness aims a "just before a fire" line at the median fire phase, and with the new render distribution the phase moves from clip to clip (as the live run found), so a line sometimes came early and the fire rightly did not wait; and a steady clip sometimes carried 0.5-0.65 s more slack than the buffer, which costs nothing. Running at 15× instead of 25× did not help (2 of 8). The checks now: per clip, a margin never below the buffer (the safe side) and the median margin within 0.3 s of it; the placement-dependent checks for the lines whose fire waited for the answer, with at least one such line required. 10 of 10 repeats pass; the "no buffer" and "fires 2 s late" mutations are still caught by two tests each (`logs/batch-028/jit-mutations.txt`).

**Commits:** `f85d7de` (the prompt), and the test fix.
