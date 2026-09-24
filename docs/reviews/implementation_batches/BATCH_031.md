# BATCH_031 — GPU session 3: X-10, X-11, X-12, X-22, X-23, X-31 (D-09, D-11)

**Status:** IN_PROGRESS · **Date:** 2026-09-24 · **Base:** `cf54ac2`

## Plan

One box (the live settings: 153 frames, the defaults of `server/server_defaults.sh`, −18 LUFS), in this order, then pull and destroy:

1. **X-10** (long-film trust for RUN-04): `main.py play --segments 300` on the box with the live config; thirty scripted lines, one every ten segments from segment 8, in rotation 0.3 s before an announced fire, 0.3 s after one, and 3 s into a segment (`steer.py --timing`, BATCH_029). Criterion (record): render slope ≤ 1 ms per clip; all RUN-04 gates.
2. **X-22, X-23, X-31, X-12** renders (`gpu_session1.py run --only`; 38 arms, about 128 clips; BATCH_030's inputs).
3. **X-11** last, since it kills and restarts the server (`x11_restart.py run`; two arms of 16 clips).

X-26's GPU half is not in the session (no reference image for the lighthouse fixture).

**Budget.** ≈ $6.73 of $30 spent; expected 70-80 minutes at about $2.7/h (≈ $3.5).
