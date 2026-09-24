# BATCH_023 — Story authority: the viewer's direction is scoped, expires, and hides the card's candidates (ARCH-08, ARCH-09)

**Status:** COMPLETED (ARCH-08, ARCH-09); ARCH-01 stays BLOCKED · **Date:** 2026-09-23 · **Base:** `030e21c`

## Plan

**Rows.** ARCH-08 and ARCH-09, `APPROVED` by the owner's D-01 (`08_OWNER_DECISIONS.md`) after X-07/X-08: momentum stays; a landing clears it only when its run came after the line; it expires after about 90 story seconds; the card's candidate beats are hidden from the Director while a viewer direction is active. ARCH-01's transcript check is re-run on the new behaviour (D-01: "re-run on the next real-Director run"). One contract (who steers while the viewer is active), one validation boundary (engine tests plus real-Director runs on X-07/X-08's harness).

**Files.** `src/graph/director_engine.py` (expiry at the plan; the trigger's origin carried to the landing), `src/story/validator.py` (`apply_showrunner(..., triggered_at=)`), `src/story/context.py` (`viewer_direction_active`, `format_plan(..., hide_candidates=)`), `src/agents/director.py`, `src/models/config.py` (`viewer_momentum_seconds: 90`), tests, `docs/ARCHITECTURE.md`, `CLAUDE.md`; evaluation: `scripts/experiments/story_authority.py`'s recorder.

**Risk.** A film that moves less once the viewer is silent (X-08 saw it for E5 without an expiry). Rollback: `viewer_momentum_seconds` very large restores "never expires"; the reset and the hiding are one revert.

## The reset rule, as implemented

The record's ARCH-08 text ("a landing clears it only when the run was spawned after `viewer_momentum_segment`") was written against the engine before ARCH-02 and ARCH-03: then a run spawned at a line's own plan predated the momentum, and a held trigger was forgotten. On today's engine both assumptions are gone: a run spawned at the answer beat's first clip already holds the line, and a replan held by `showrunner_min_gap` spawns segments later. X-07's own evidence shows the second case: in the `base` run the line of segment 28 raised `needs_replan`, the gap held it, it spawned at 32 and its card cleared the line's momentum at 34, after which five beats followed the card. A literal "spawned after" test (32 > 28) would clear it again. The rule is therefore keyed to **where the run's trigger arose**: the plan that raised it, or, for a held replan, the plan it was held since. A card clears the direction only if that is after the line's segment. With no trigger given (the bootstrap, direct callers) the spawn segment decides, as before.

## Result

| File | Change |
|---|---|
| `src/graph/director_engine.py` | At each plan, after a finished run lands: a direction older than `viewer_momentum_seconds` of film is dropped (note in the transcript, `momentum_expired` in the timing log). `triggered_at` (this plan, or a held replan's `since`) travels with the pending spawn, the run's metadata and the fail-soft landing replay to `apply_showrunner`. |
| `src/story/validator.py` | `apply_showrunner(..., triggered_at=None)`: a new card clears the momentum only if the trigger arose after the momentum's segment. |
| `src/story/context.py` | `viewer_direction_active(world, current_input)`: the beat answers a line, or momentum is set. `format_plan(..., hide_candidates=False)`: the Director's copy replaces the candidate list with "hidden while the viewer's direction is active; they come back when it expires". The Showrunner's copy is unchanged. |
| `src/agents/director.py` | the Director's plan section hides the candidates while a direction is active (the answer beat included: the line is an active direction; the X-08 E5 arm hid them on momentum only) |
| `src/models/config.py` | `viewer_momentum_seconds: 90.0` |
| `tests/test_story_authority.py` (new, 7) | the line's own replan, immediate and held, leaves the direction; a later-triggered card clears it; expiry at 13 s over 6.04-s clips; candidates hidden on the answer beat and while active, back after expiry; the Showrunner's copy; `apply_showrunner`'s keying |
| `scripts/experiments/story_authority.py` | the recorder's `candidates_seen` follows production for `base` |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | the behaviour and its source |

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | 120 passed (113 + 7) |
| Mutations (`logs/batch-023/mutations.txt`) | the old reset (always clear) caught by 3 tests; the held trigger's origin ignored by 1; no expiry by 2; candidates always shown by 1 |
| Real Director, X-07/X-08's harness on production code (`logs/experiments/story-authority-d01/`): `directions` × 3, `silence` × 5, 40 segments each, gpt-4.1 priority tier, judged by `judge_authority.py` (same judge, prompt and seed as X-07/X-08) | below |

### Real-Director reading (D-01 production against the five production runs of X-07/X-08, the same schedules, harness and judge)

| Measure | Before D-01 (5 runs) | D-01 (8 runs) | X-08 E5 arm (3 runs) |
|---|---|---|---|
| Beats following a card **older than** the active direction (ARCH-01's check; a card written from the line itself builds on it and is not counted) | 7 (2 runs; candidates visible) | **3 in the first 5 runs** (2 runs; candidates hidden, the card's goal visible), 0 in the 3 later silence runs | 1 |
| Card beats within 90 s of a line after the direction was gone | 7 | **2** (both in base-directions-1, after the permitted clear at 38) | 1 |
| Direction held (directions schedule, story seconds) | 55.7 | 59.1 | 56.4 |
| Momentum cleared by a card landing | 6 times in X-07's momentum runs | 1 (base-directions-1: a `scene_done` raised at segment 36, eight segments after the line of 28: permitted); the other clears were 5 expiries and new lines | — |
| Structural changes per minute after the last line (silence schedule) | 1.53, 0.76 | 0.0, 0.58 (both lost their second line, below), 0.38, 0.40, 0.76 | 0.38, 0.76 |
| REQUIRED notes / stagnation replans | 0 / 0 | 1 / 0 | 0 / 0 |
| Answer beats ≤ 2 clips (ARCH-11) | 13 of 13 | 17 of 17 | — |

- **ARCH-08: COMPLETED.** The replan a line provokes no longer erases it (one card cleared a direction in eight runs, and its trigger came eight segments after the line; the early-clearing snap-backs fell from 7 beats to 2), and directions expire (five expiries, all at 90 s of film).
- **ARCH-09: COMPLETED.** Candidates are hidden exactly while a direction is active and return on expiry; card-driven beats during an active direction fell from 7 to 3. Movement after the last line in the three clean silence runs (0.38, 0.40, 0.76 per minute) overlaps the earlier production runs (1.53, 0.76) and the E5 arm (0.38, 0.76); the two runs at 0.0 and 0.58 lost their second line (below) and kept playing on the boat with the candidates visible from segment 22: not a stall (beats of continuous physical comedy, no REQUIRED note), but no structural change. Small samples; recorded, not claimed.
- **ARCH-01: still BLOCKED.** Three beats in two of the first five runs returned to an older card's *goal* 50-60 s after the viewer's latest line, with the candidates hidden (base-directions-2 beat 25, base-directions-3 beats 22-23: the captain card after "he gets sleepy and tries to build himself a bed"). The rule is not yet "no beat". The remaining lever is the card's goal, which ARCH-09 leaves visible; proposed to the owner (`08_DECISION_UPDATE_PROPOSALS.md`).

### Incidents in the runs

- OpenAI connection timeouts from this network: 1-2 Director calls failed in each of the first five runs (answer-call means 7.3 and 9.9 s include them; the three later runs had none, 2.7-4.0 s). A failed call holds the picture for one clip.
- **A failed Director call on a plan that answers a viewer line loses the line.** In base-silence-1 and -2 the call answering "Everything he touches starts to bounce like rubber" timed out; the fallback beat held the picture and the line was never answered (`plans.jsonl`: beat `null` at segment 14; the momentum stayed the boat line of segment 6). Pre-existing (the fallback path predates this batch) and outside every current row; proposed to the owner with the fix (re-answer the line at the next plan).

## Deviations

- The reset is keyed to the trigger's origin, not the spawn segment (above).
- Candidates are also hidden on the answer beat itself (the E5 arm hid them on momentum only).
- Three more silence runs than planned (the first two lost a line to timeouts; the movement reading needed clean runs).

## Next

RUN-14 (render-time headroom): prepare the encode and response-path changes locally for the next GPU session.

**Commit:** this batch's.
