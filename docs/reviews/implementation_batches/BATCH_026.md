# BATCH_026 — The card pauses during a viewer's direction (D-05), and a failed answer no longer loses the line (ARCH-20)

**Status:** COMPLETED · **Date:** 2026-09-23 · **Base:** `6488585`

## Plan

The owner's D-05 extends ARCH-09: while a viewer's direction is active the Director's plan section pauses the whole scene card, not only its candidates, because BATCH_023's three remaining snap-backs followed the card's goal. D-06 approves ARCH-20: a line whose answering Director call fails must be answered at the next plan, not dropped (2 of 8 runs lost a line to network timeouts in BATCH_023). Both touch the viewer's path; one validation boundary (tests, then the same real-Director runs).

## Result

| File | Change |
|---|---|
| `src/story/context.py` | `format_plan(..., paused=False)`: the Director's copy shows "Scene N (…): paused while the viewer's direction is active; it resumes when the direction expires" with no goal, tension, exit condition, intensity or candidates; the Showrunner's copy is whole |
| `src/agents/director.py` | `paused=viewer_direction_active(...)` |
| `src/graph/director_engine.py` | `last_plan_fallback`: the plan answered a line and the Director's call failed; the line's pending record is taken back before the held clip is emitted (emitting it would record the line as deferred, and the next answer would resolve both records); the transcript note says the line is answered next |
| `src/eval/runner.py` | live loop: after a fallback plan the line joins the next plan (`carry_text`, its ids stay pending), `line_requeued` in the timing log, a status to the viewer, no ARCH-06 join with the failed answer; terminal loop: the line is prepended to the next turn's input |
| `tests/test_story_authority.py` | the paused card (goal and candidates gone for segments 2-4, back whole at 5); the Showrunner's copy |
| `tests/test_lost_line.py` (new, 2) | a backend that fails once the way `OpenAIBackend` reports a timeout (empty response): live loop and terminal loop answer the line at the next plan, one intervention, absorbed; the failed segment held the picture |
| `scripts/latency_probe.py` | `--frames` and `--overlap` for warm mode (GPU session 2 probes 153 frames with the ratio over the 144 aired) |
| `docs/reviews/08_OWNER_DECISIONS.md` | D-05..D-08, ARCH-20 |

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | 132 passed |
| Mutations (`logs/batch-026/mutations.txt`) | the engine keeping the pending record: caught by 2; the live loop dropping the line: 1; the terminal loop dropping it: 1; the card never paused: 1 |
| Real Director, X-07/X-08's harness on production code (`logs/experiments/story-authority-d05/`: directions × 3, silence × 2), same judge | below |

| Measure | Before D-01 (5 runs) | D-01 (8 runs) | D-05 (5 runs) |
|---|---|---|---|
| Lines followed by a return to the card (judge) | 4/9 · 1/2 (… directions · silence) | 4/9 · 3/8 | **2/9 · 1/4** |
| Beats labelled "card" with an older card during an active direction | 7 | 3 (goal visible) | 2 (**card not shown**: the Director drifted on its own, "a brick appears", matched to a generic candidate) |
| Card beats within 90 s of a line with no direction active | 7 | 2 | **0** |
| Direction held, directions schedule (s) | 55.7 | 59.1 | 58.4 |
| Structural changes per minute after the last line (directions · silence) | 1.13 · 1.15 | 0.58 · 0.42 | **1.13 · 0.76** |
| Longest unchanged stretch after the last line (silence, s) | 102.7 | 205.4 | 139.0 |
| Director calls (answer · other, s) | 3.68 · 3.51 | 7.3 · 3.52 (timeouts) | 2.51 · 2.47 |

With the card paused nothing in the Director's context competes with a viewer's direction, and after it expires the whole card returns: movement after the last line is back to the level before D-01. No Director call failed in these runs, so ARCH-20 was exercised by its tests only.

## Rows

- ARCH-09 (with D-05): COMPLETED.
- ARCH-20: COMPLETED.
- ARCH-01: its check is "transcript review of the next live run": the D-05 runs show no beat that can follow a card during a direction (the card is not shown); the live run of GPU session 2 is reviewed for it.

## Next

GPU session 2 (D-07): RUN-14's arms, the seam rollout if there is headroom, the live run.

**Commit:** this batch's.
