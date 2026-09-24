# BATCH_031 — GPU session 3: X-10, X-11, X-12, X-22, X-23, X-31 (D-09, D-11)

**Status:** OWNER_REVIEW_REQUIRED · **Date:** 2026-09-24 · **Base:** `cf54ac2` (shipped `8e024b1`)

## Plan

One box (the live settings: 153 frames, the defaults of `server/server_defaults.sh`, −18 LUFS), in this order, then pull and destroy:

1. **X-10** (long-film trust for RUN-04): `main.py play --segments 300` on the box with the live config; thirty scripted lines, one every ten segments from segment 8, in rotation 0.3 s before an announced fire, 0.3 s after one, and 3 s into a segment (`steer.py --timing`, BATCH_029). Criterion (record): render slope ≤ 1 ms per clip; all RUN-04 gates.
2. **X-22, X-23, X-31, X-12** renders (`gpu_session1.py run --only`; 38 arms, about 128 clips; BATCH_030's inputs).
3. **X-11** last, since it kills and restarts the server (`x11_restart.py run`; two arms of 16 clips).

X-26's GPU half is not in the session (no reference image for the lighthouse fixture).

**Budget.** ≈ $6.73 of $30 spent; expected 70-80 minutes at about $2.7/h (≈ $3.5).

## Result

Vast offer 52422226 (H100 NVL, Virginia, $3.08/h; a different host from sessions 1 and 2), 13:40-14:49 UTC: 69 minutes, **≈ $3.55** (≈ $10.28 of $30). No instance left. Results and dispositions: `docs/reviews/experiments/session3.md`; proposals in `08_DECISION_UPDATE_PROPOSALS.md`.

- **X-10:** 300 clips; render slope −0.013 ms per clip, p95 0.756 (NVL); 3 stalls, all from Director calls of 6-14 s (the Director averaged 4.7 s that morning); steering p50 14.9 s from the adversarial line placement.
- **X-11:** the window restore carries the scene through a server restart (jump 1.66 against 66.1; content unchanged); recovery +0.4 s.
- **X-12:** the supplied master opens wide and the model zooms in over two seconds.
- **X-22:** no artefact difference with and without the renderer rules; requests honoured.
- **X-23:** the clause changes nothing it is for.
- **X-31:** 6 of 6 requested cuts, 0 of 12 unrequested (by eye).

## Incidents

- The driver's wait for a remote marker went through the ssh output filter (`grep -v`), which exits 1 when it prints nothing; with `pipefail` every wait ran to its timeout. X-10 finished at about 14:24; the driver sat until 14:35, when it was stopped and the remaining steps re-run with a direct ssh wait (about 11 minutes of rental lost). Session 2's live-run wait had the same bug and ended by its 15-minute timeout.
- `pkill -f` on the driver's name also killed the shell that ran it (the known self-match).

## Validation

- The analyses: `scripts/experiments/session3_analyse.py` (X-22 pairs, X-23 audio and ASR, X-31 cuts, X-12 framing), `scripts/experiments/x11_restart.py analyse`, `scripts/bench_report.py` on X-10; the by-eye calls from the sheets.
- `python -m tests.run`: 136 passed (unchanged by this batch; the session code was committed in BATCH_029/030).
