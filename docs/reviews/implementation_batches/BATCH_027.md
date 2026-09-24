# BATCH_027 — GPU session 2: RUN-14's arms, the seam rollout, the live run (D-07)

**Status:** COMPLETED · **Date:** 2026-09-23/24 · **Base:** `d14f142` (rollout `1ac0f09`)

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

## Result

Vast offer 28160519 (the same H100 80GB HBM3 SXM host as session 1; Xeon Gold 6448Y, 128 threads), instance 52330473, 01:22-02:31 UTC: 68.6 minutes, **≈ $3.05**; ≈ $6.73 of the $30 cap spent in total. No instance left. `VAST_ENV='-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video'` did not reach the container (it reported `compute,utility`): NVENC stays unavailable on Vast.

### RUN-14's arms (27 warm renders each, session A's prompts, ratio over the aired frames)

| Arm | Settings | Wall ratio p50 / p95 / max |
|---|---|---|
| A0 | old path, 145 frames | 0.801 / 0.847 / 0.857 |
| A0 | old path, 153 frames | 0.842 / **0.890** / 0.899 |
| A1 | frames converted on the GPU | 0.686 / 0.706 / 0.708 |
| A2 | + YUV 4:2:0 piped | 0.663 / 0.682 / 0.687 |
| **A3** | + 8 x264 threads | 0.652 / **0.666** / 0.672 |
| A4 | + 16 x264 threads | 0.648 / 0.673 / 0.675 |
| A5 | "nvenc" (fell back to x264: no video capability) | 0.662 / 0.686 / 0.696 |
| A6 | A3 with the −18 LUFS ceiling (the live settings) | 0.678 / 0.691 / 0.698 |

The frames on the GPU took almost all of it: the 390 MB float32 copy the pipeline made inside `pipe()` and the numpy conversion after it. A3 became the server's defaults (`server/server_defaults.sh`); the rollout rule (p95 ≤ 0.83, max ≤ 0.92 at 153 frames) was met with room, so the live config switched to `seam: "overlap"` and `audio_ceiling_lufs: -18` (`1ac0f09`). 145 frames was not re-probed on the new settings (the live config no longer uses it).

### The live run (phase 6; `logs/live-box/session2/`)

64 clips, 153 frames each, on the box next to the server; nine scripted lines. `scripts/bench_report.py`:

| Gate | Value | Threshold | Result |
|---|---|---|---|
| Render ratio p50 / p95 / max | 0.669 / 0.689 / 0.696 | 0.82 / 0.85 / 0.92 | PASS |
| Render trend | −0.9 ms per clip (61 warm) | ≤ 1 ms over ≥ 150 clips | n/a (needs X-10's length) |
| Mid-film compiles | 0 | 0 | PASS |
| Stalls | 0 | 0 | PASS |
| Minimum queue margin | 3.43 s | ≥ 1 s | PASS |
| Steering p50 / p95 | 11.5 / 15.6 s | 12.5 / 16 s | PASS |
| First picture | 15.2 s | 25 s | PASS |
| Hold minus aired media | 0.0 ms | 5 ms | PASS (after the fix below) |

Seam gates, `scripts/seam_check.py` on 24 of the run's clips in headless Chromium 151 (overlap mode): duplicate frames shown 0; dip 0.0 % (max 0.87 %); swap p95 6.5 ms; reloads on `playing` 0 — all PASS (one of 22 switches reused a pool element mid-transition, with no visible dip). `scripts/audio_check.py` on all 64: 0 clips above −18 LUFS, 0 true-peak overs, narrated-to-next 13.45 LU (≤ 14), 0 clicks — PASS; seam step 22.2 / 63.6 dB and 23.9 silence holes per minute — FAIL, as X-02 found and D-03 accepted (LTX's clips end in silence; AUD-09 deferred).

Stage means: generate 3.39 s (transformer 2.42 s, VAE 0.60 s), frames on the GPU 0.11 s, encode 0.49 s (1.1 s before), client overhead 0.03 s (0.31 s before: it was the host-side uint8 conversion). Director calls 2.80 s mean (answering 2.75 s); 40 plans, 1 cancelled by a line, 1 re-asked, 0 failed. All nine lines absorbed and landed at N+2 or N+3; none dropped.

**ARCH-01's transcript review.** Lines came about every 40 s, inside the 90-s expiry, so a direction was active from the first line on and the Director saw the card paused throughout (D-05); every line was absorbed and developed. No beat could follow a card during a direction. One finding: every absorbed answer set `delivered_request` to its own line's segment, which the validator ignores with a warning (`delivered_request N names no deferred request`); harmless, a prompt clarification would remove it.

**Protocol deviation.** RUN-04 asks for about a third of the lines within 1 s of a render's fire. The lines were timed at +2.3 s into a clip, the expected phase of the fire two clips ahead, but the fire moved between +1.3 and +4.5 s with the beat structure: **1 of 9** lines landed within 1 s of a fire (0.85 s after it). The adversarial case (a line just before a fire) is covered offline by the JIT simulation, not by this run. A fire-aware scripted viewer (reading the box's `timing.jsonl`) would fix it for X-10.

### Baseline

`benchmarks/runtime/baseline.json` is now this run (label `session2-2026-09-24`; shape 153/144, stage timings, seams); the reconstructed session-A record moved to `benchmarks/runtime/2026-09-18-session-a.json`, as its provenance note said. The README table is regenerated.

## Files changed

| File | Change |
|---|---|
| `server/server_defaults.sh` | `LTX2_GPU_FRAMES=1`, `LTX2_PIPE_FORMAT=yuv420p`, `LTX2_X264_THREADS=8` |
| `configs/director.live.yaml` | `seam: "overlap"`, `audio_ceiling_lufs: -18` |
| `scripts/bench_report.py` | the hold is compared with the aired media (the file minus the frames the shape does not deliver) |
| `scripts/latency_probe.py` | warm mode defaults its overlap from the baseline's shape |
| `scripts/steer.py` | `SEG+SECONDS:text` (BATCH_027 plan commit) |
| `benchmarks/runtime/baseline.json`, `benchmarks/runtime/2026-09-18-session-a.json`, `README.md` | the new baseline, the old one kept, the table |
| `tests/jit_sim.py` | renders the config's selected shape; `clip_length()` is the delivered length |
| `tests/test_jit.py`, `tests/test_replay.py`, `tests/test_experiment_arms.py`, `tests/test_shape.py`, `tests/test_seam_design.py`, `tests/test_bench.py` | derived instead of session-A constants; the probe and deadline tests on session A's record as a fixture; the line-timing check at 0.5 s (20 ms simulated at 25×); the live shape; the overlap hold |
| `CLAUDE.md` | the seams are live |

## Validation

- `python -m tests.run`: 133 passed; `test_jit` 5 of 5 repeats on a quiet machine (under load, with the live run's sessions open, it failed on its 0.3-s timing checks in both seams; widened only for the line-timing read-back).
- The live run's gates, seams and audio as above.

## Rows

- RUN-14: COMPLETED. LTX-12, AUD-02, AUD-03: COMPLETED (live). RUN-04: COMPLETED for the canonical shape (the trend gate needs X-10's ≥ 150 clips; the near-fire line protocol was missed). ARCH-01: COMPLETED.

**Commit:** this batch's.
