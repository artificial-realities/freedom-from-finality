# BATCH_032 — Restart recovery from the last clip's tail by default (RUN-10, D-12)

**Status:** COMPLETED · **Date:** 2026-09-24 · **Base:** `d4e18e6`

## Plan and result

The owner's D-12 on X-11: the window restore becomes the default after a server restart, the reference used only before the first clip, and the Director told when a re-seed happens anyway (the last clip's tail could not be read).

| File | Change |
|---|---|
| `src/models/config.py`, `src/i2v/ltxv.py`, `src/i2v/__init__.py` | `ltxv_restart_recovery` defaults to `"window"`; `"reference"` stays as the other value |
| `src/graph/director_engine.py` | `note_reseed(segment)`: the next plan's picture section starts with "the renderer restarted and the picture was re-seeded from the reference image: {protagonist} is back in the reference pose and nothing else from before is in the picture …"; kept until a plan is answered (a cancelled call restarts with it) |
| `src/eval/runner.py` | a render that came back re-seeded writes `reseeded` to the timing log and calls `note_reseed` |
| `tests/test_restart_recovery.py` | the default is the window (config, live config, client); the Director hears of a re-seed once |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | the restart behaviour |

## Validation

- `python -m tests.run`: 137 passed.
- The window path's behaviour on hardware is X-11 (`docs/reviews/experiments/session3.md`).

**Commit:** this batch's.
