# BATCH_027 — GPU session 2: RUN-14's arms, the seam rollout, the live run (D-07)

**Status:** IN_PROGRESS · **Date:** 2026-09-23 · **Base:** `d14f142`

## Plan

**Scope (the owner's D-07).** One warmed box: measure RUN-14's settings; if one gives the tail headroom, roll out the overlap seam (D-03: 153 frames, −18 LUFS); then the phase-6 live run (60+ clips, scripted lines, about a third within 1 s of a render's fire) against RUN-04's gates, and ARCH-01's transcript review. X-10..X-12, X-18, X-31 are not in this session.

**RUN-14's arms** (`scripts/latency_probe.py --warm 30` on the box with session A's prompts, `--frames 153 --overlap 9` for the seam shape; `vast_run.sh restart` between arms, warm frames 145 and 153; `LTX2_ENCODER=x264` except the NVENC arm):

| Arm | Settings | Shapes |
|---|---|---|
| A0 | today's defaults | 145, 153 |
| A1 | `LTX2_GPU_FRAMES=1` | 153 |
| A2 | A1 + `LTX2_PIPE_FORMAT=yuv420p` | 153 |
| A3 | A2 + `LTX2_X264_THREADS=8` | 153 |
| A4 | A2 + `LTX2_X264_THREADS=16` | 153 |
| A5 | A2 with `LTX2_ENCODER=nvenc`, if the box was granted the video capability (`VAST_ENV`) | 153 |

**Rollout rule.** The overlap seam goes live if the best arm's wall p95 at 153 frames (over the 144 aired) is at most 0.85 with margin (≤ 0.83) and its max at most 0.92; its settings go into `server/server_defaults.sh`, the live config's two lines are uncommented, the server restarts with the ceiling, and a last probe confirms the setting with the ceiling on. Otherwise the live run stays on the fade seam with the best setting, and the owner is told.

**Live run.** `main.py play --config configs/director.live.yaml --player web --segments 64` on the box, `scripts/steer.py` posting nine lines (three at +2.3 s into a segment, near the fire of the clip two ahead). Then `bench_report` on its `timing.jsonl` (RUN-04), `seam_check` on its clips in headless Chromium (seam gates), `audio_check`, and the transcript read for ARCH-01.

**Budget.** ≈ $3.68 of $30 spent; expected 60-75 minutes at about $2.7/h (≈ $3). Stop on repeated infrastructure failures.
