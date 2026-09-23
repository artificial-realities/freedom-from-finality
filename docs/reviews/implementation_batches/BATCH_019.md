# BATCH_019 — Local experiments X-02 and X-13, and X-01's listening material

**Status:** OWNER_REVIEW_REQUIRED (X-02, X-13 offline RESULT_READY); X-01 BLOCKED on listeners · **Date:** 2026-09-23 · **Base:** `96b58de`

## Plan

X-02's prerequisites are done (RUN-01; the seam and audio checks, BATCH_004); its method needs the AUD-02 / AUD-03 / LTX-12 candidate as an off-by-default variant, which the ledger lists as X-02's permitted preparation. X-13's offline half needs AUD-03's candidate encode. X-01 needs listeners, which the owner supplies; its material can be prepared now. Nothing gated is enabled: the page defaults to `seam: "fade"`, the server's ceiling needs `LTX2_AUDIO_CEILING_LUFS`.

## Result

| File | Change |
|---|---|
| `src/web/player.html`, `src/web/server.py`, `src/playback/player.py` | The page's `overlap` seam mode (three-element pool, cut at the overlap by media time, equal-power crossfade, speech-safe, first-clip fade-in; a third `<video>` element that fade mode never uses); `WebPlayer(seam="fade")`, the mode in `hello`; `Playable.overlap_seconds`, `Playable.speech`. |
| `server/audio_ceiling.py` (new), `server/ltx2_server.py`, `server/vast_run.sh`, `src/i2v/ltxv.py` | AUD-03's candidate: pad, ebur128, one static never-boosting gain to the ceiling and under −1 dBTP; the server applies it only with `LTX2_AUDIO_CEILING_LUFS`; `X-Audio-LUFS/TP/Gain-dB` headers, typed for the timing log; `vast_run.sh` copies the module. |
| `scripts/seam_check.py` | `--seam overlap --overlap-frames N`. |
| `scripts/experiments/ceiling.py` (new) | The candidate encode applied to a run's clips offline. |
| `scripts/experiments/x01_pairs.py` (new) | X-01's blind pairs, controls, answer key and response sheet. |
| `tests/test_seam_candidate.py` (new) | The gain rules, the measurement against a synthetic tone, the modes off by default. |
| `docs/reviews/experiments/X-02-X-13.md`, `docs/reviews/experiments/X-01.md`, `docs/reviews/08_DECISION_UPDATE_PROPOSALS.md` | Reports and proposals. |

Results: see the report. The picture gates pass for the candidate; the sound's seam-step criteria fail in every arm (content, not the join); both ceilings meet X-13's offline criteria.

## Validation

- `python -m tests.run`: **114 passed, 0 failed** (111 before; 3 new).
- `py_compile` on the server and the ceiling module; `bash -n server/vast_run.sh`.
- The candidate page was debugged in headless Chromium (a first run showed the incoming element reused by the next prefetch before its cut: fixed by reserving it) and the final numbers come from clean runs.
- Not run: the server's ceiling on hardware (it runs first in a GPU session, only if an experiment sets it).

## Commit message

```
X-02 and X-13: the seam candidate in the browser and the loudness ceiling offline

The page gains an off-by-default overlap seam (three elements, a cut at
the overlap by media time, an equal-power audio crossfade) and the
server an off-by-default loudness ceiling (pad, one static never-
boosting gain, under -1 dBTP), shared with an offline tool. In headless
Chromium on session A the candidate passes every picture gate (no
duplicate frames, no dip, no reloads); the played mix still fails the
seam-step criterion because clips end in silence and the next opens on
sound; both ceilings meet X-13's offline criteria. X-01's blind pairs
are ready for listeners. Results and proposals for the owner are in
08_DECISION_UPDATE_PROPOSALS.md.
```
