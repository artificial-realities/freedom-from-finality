# BATCH_025 — The overlap seam as a production option (LTX-12, AUD-02, AUD-03)

**Status:** COMPLETED as built and validated locally; the rows stay BLOCKED on their rollout (D-03: after RUN-14's GPU check) · **Date:** 2026-09-23 · **Base:** `9d675bd`

## Plan

**Rows.** LTX-12, AUD-02 and AUD-03, `APPROVED` as one design by the owner's D-03: render 153 frames with the 9-frame overlap kept in the file; the player cuts at n − overlap with an audio crossfade over the overlap; the server pads audio to the video under a −18 LUFS / −1 dBTP ceiling; rollout only after RUN-14. RUN-12 (MSE) is closed. X-02 built the parts as an off-by-default candidate (the page's overlap mode, the server's ceiling); what was missing for production is the contract: one config switch that sets the shape, the hold, the page and the server together, and the per-clip data the page needs from the live loop.

**Risk.** A config that switches the page but not the shape (or the server's ceiling) would cut at the wrong frame or leave loudness as it was: one switch drives all three, and the client notices a server whose ceiling differs. Rollback: `seam: "fade"`.

## Result

| File | Change |
|---|---|
| `src/models/config.py` | `seam: "fade" | "overlap"` (default "fade", refused otherwise); `audio_ceiling_lufs` (None = off) |
| `src/i2v/shape.py` | `ClipShape.overlapped(seconds, overlap_frames)`: new frames + overlap on the 8k+1 grid, the new frames delivered (6 s at 24 fps: 153 rendered, 144 delivered, 6.0 s); `overlap_seconds`; `ShapeRegistry.from_config` uses it with `seam: "overlap"` (so the Director, the story clock, the render request, the player's hold and the warm-up list all read it) |
| `src/eval/runner.py` | after a render, `Playable.overlap_seconds` = the shape's overlap unless the clip was seeded from the reference (the run's first, or a server restart's: `last_render["reference"]`); `Playable.speech` from the engine; the client compares the server's `/health` ceiling with the config and writes `ceiling_mismatch` (a warning, not a refusal) |
| `src/state/story_state.py`, `src/graph/director_engine.py` | `current_speech`: the clip carries its beat's voice-over or dialogue |
| `main.py` | `WebPlayer(..., seam=config.seam)` |
| `src/i2v/ltxv.py`, `server/ltx2_server.py` | `/health` reports `audio_ceiling_lufs`; the client passes it on when the server reports it |
| `src/i2v/replay.py` | the first replayed clip counts as reference-seeded (as a real run's) |
| `server/vast_run.sh` | the server's `LTX2_AUDIO_CEILING_LUFS` comes from the config (`AR_CONFIG`, default the live one) unless set in the environment, as the warm frames do |
| `configs/director.live.yaml` | `seam: "overlap"` and `audio_ceiling_lufs: -18`, commented until the rollout |
| `src/web/server.py`, `src/web/player.html`, `src/playback/player.py` | comments: the overlap mode is a production option (D-03), no longer an X-02 candidate |
| `tests/test_seam_design.py` (new, 4) | the shape; the warm list and the config wiring; a replayed 153-frame film: holds 6.0 s, overlap 0 then 0.375 s, speech marked where the narrator's line lands and not elsewhere, no shape mismatch; the ceiling mismatch noticed once (server off against −18; not for an equal or an unreported ceiling) |
| `tests/test_seam_candidate.py` | renamed: the page is `fade` unless the config asks |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | the two seams and the rollout condition |

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | 130 passed (126 + 4) |
| `scripts/seam_check.py` on GPU session 1's 153-frame chain (`x03/frames-153`, clips 1-12), overlap mode, real `WebPlayer` in headless Chromium 151 (`logs/batch-025/seam-153-overlap/`, `seam-153-files/`) | duplicate frames shown per seam **0** (file overlap 8-9 frames where determinable, hidden by the cut); dip median **0.04 %**, max 0.23 %; swap p95 **4.7 ms**; reloads on `playing` **0**; outgoing element replaced during a fade 0 |
| The live config loads with `fade`/None; an overlap config warms `153,153,153` and passes −18 to the server (`vast_run.sh`'s derivation, sourced in a shell) | pass |
| **On hardware** | not run here: X-03 (GPU session 1) measured the 153-frame render and its A/V under the ceiling; the rollout's live check is the phase-6 run |

The played-mix seam step (X-02's sound criterion) was not re-measured: D-03 accepted it as unmet (LTX's clips end in silence and the next opens on sound).

## Rollout (after RUN-14 on GPU session 2)

Uncomment the two lines in `configs/director.live.yaml`; `vast_run.sh up` then warms 153 frames and starts the server under −18 LUFS; the client checks both. The phase-6 live run measures it (RUN-04's gates, seam gates, `bench_report`).

## Next

With every decided row built, the remaining work needs a GPU session (RUN-14's arms, the seam rollout, the live run, X-10/X-11/X-12, X-18/X-31), listeners (X-01, X-13's listening half) or the owner (ARCH-01's goal proposal, the lost-line fix).

**Commit:** this batch's.
