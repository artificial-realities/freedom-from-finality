# BATCH_030 — GPU session 3's arms: RUN-10's restart recovery (X-11), X-12, X-22, X-23, X-31

**Status:** COMPLETED as preparation (D-09, D-11) · **Date:** 2026-09-24 · **Base:** `d3bcf9f`

## Plan

Everything GPU session 3 runs, prepared and tested locally before the rental (record §2, Q8): X-10 (BATCH_029's fire-aware viewer), X-11 (RUN-10's arm, behind a flag: the row says "behind a flag until X-11"), X-12 (the owner's 16:9 master, D-11), X-22 and X-23 (post-implementation checks), X-31 (native multi-shot, if the installed model follows the documented convention). X-26's GPU half is not prepared: the lighthouse fixture has no reference image (an asset nobody has made), so it waits for one.

## Result

| File | Change |
|---|---|
| `src/models/config.py`, `src/i2v/__init__.py`, `src/eval/runner.py`, `src/i2v/ltxv.py` | `ltxv_restart_recovery: "reference"` (default, today) or `"window"` (RUN-10's arm, X-11 only): on a restarted server's "reference required", the client sends the reference and its last clip's last 9 frames (decoded with ffmpeg at the render size), and records `restored` instead of `reseeded` (such a clip continues the picture, so the overlap seam applies to it); `X-Session-Epoch` recorded |
| `server/ltx2_server.py` | `SESSION_EPOCH` (a new one per process) in `/health` and `X-Session-Epoch`; `window_frames` in the request: a fresh session continues from them (resized to the render size) instead of the reference |
| `scripts/experiments/x11_restart.py` (new) | two arms × session A's segments 45-60 (the princess and the crown on screen), the server killed after clip 10 and restarted with `server_defaults.sh`; restart and recovery times; analysis of the join at the restart against the other joins, on-screen content before and after, a sheet of clips 9-13 |
| `scripts/experiments/session3_prepare.py` (new) | X-22: ten wild lines answered by the real Director with and without the "Writing for the video model" section, compiled; X-23: session A 1-20 with and without "Lego Man makes no vocal sound." on clips without speech; X-31: three scenes as one take and with cuts named in prose, two seeds, each followed by a continuing clip |
| `scripts/experiments/gpu_session1.py` | `run --only` takes any experiment folder; an arm may name its own reference image (X-12) |
| `tests/test_restart_recovery.py` (new, 3) | the window arm sends the reference and nine tail frames and records `restored`, the epoch changes; the default re-seeds; the flag is off and refuses other values |

Inputs (gitignored): `logs/experiments/gpu3/` — 38 arms (X-22: 20; X-23: 2; X-31: 12; X-12: 4, with `legoman-square.png` from git and `legoman-master.png`, the owner's master).

## Validation

- `python -m tests.run`: 136 passed.
- The server's window restore on CPU torch with a fake pipeline (scratch smoke test): a fresh session given the reference and nine 128 × 64 frames conditions on nine frames at the render size; without them, on the reference; the epoch is set.
- The X-22 preparation ran the real Director: 20 of 20 answers absorbed.
- Not run: `x11_restart.py run` (it kills and restarts the real server; GPU session 3).

**Commit:** this batch's.
