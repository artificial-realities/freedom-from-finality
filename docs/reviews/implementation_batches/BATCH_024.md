# BATCH_024 — Render-time headroom, prepared (RUN-14)

**Status:** COMPLETED as preparation; RUN-14 BLOCKED on its GPU validation (GPU session 2) · **Date:** 2026-09-23 · **Base:** `47f9a29`

## Plan

**Row.** RUN-14 (`08_OWNER_DECISIONS.md`, D-04): cut the per-clip time that is not the model, prepared and tested locally, measured on the next GPU session before the seam rollout (D-03) and the live run. GPU session 1 put 1.4 s of a 4.9 s clip outside the model: the CPU encode (1.0-1.3 s) and about 0.3 s between the server's measured stages and the client's receipt.

**Where the time goes (read from the code).** `pipe()` returns float frames on the host (`output_type="np"`: the pipeline copies 390 MB of float32 off the GPU inside the measured generate time); the server then converts them to uint8 with numpy (four passes, after the generate timer and before the encode timer: the "client overhead"); ffmpeg converts RGB to YUV 4:2:0 on the CPU before x264; x264 runs with `-threads 0`, which on the session-1 box (128 threads) means ffmpeg's own choice of far more threads than a 640×352 clip can use.

**Risk.** A changed picture (colour) or sync. Every step is a server setting, off by default; the bytes of the default path are unchanged. Rollback: the settings.

## Result

| File | Change |
|---|---|
| `server/encode.py` (new) | `frames_to_uint8` (a GPU tensor converted where it lives, the same float32 arithmetic as the host path, then copied; numpy takes the old path); `yuv420p_planes` (BT.601 limited range, ffmpeg's default matrix and range; chroma the mean of each 2×2 block; on the GPU for a tensor); `encode_mp4` (moved from the server; pipes RGB or the YUV planes; `threads`) |
| `server/ltx2_server.py` | `LTX2_GPU_FRAMES` ("1": `output_type="pt"`, frames converted on the GPU; default "0"), `LTX2_PIPE_FORMAT` ("yuv420p" pipes the planes; needs GPU frames, refused otherwise: the host conversion costs 1.3-6.8 s), `LTX2_X264_THREADS` (default 0); the conversion timed as `post` (in `X-Timings` and `X-Post-S`); a failed GPU conversion falls back to the host for that clip and pipes RGB; `/health` reports the three settings |
| `src/i2v/ltxv.py` | `X-Post-S` recorded in the timing log's `render_done` |
| `server/vast_run.sh` | ships `encode.py`; `VAST_ENV` passes container options to `vastai create instance` (for `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`, h264_nvenc) |
| `server/server_defaults.sh` | notes the three settings stay at the code's defaults until measured |
| `tests/test_encode.py` (new, 6) | host bytes unchanged; GPU bytes equal host bytes for (B, F, C, H, W), (F, H, W, C) and bf16 (torch halves skip without torch); planes against ffmpeg's conversion (luma ±1, chroma mean < 1); a YUV-piped clip no further from the source than an RGB-piped one, same frame and audio counts; threads reach x264; odd sizes refused |
| `tests/test_seam_candidate.py` | its source check reads the ceiling setting in `server/encode.py` |

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` | 126 passed (120 + 6); the torch halves skipped |
| `tests/test_encode.py` under a CPU torch build (`logs/_scratch/torchcpu`, gitignored) | 6 of 6, the torch halves included (GPU-path bytes identical to the host path, device and host planes identical) |
| The server's changed path end to end on CPU torch with a fake pipeline (scratch smoke test: `_run_pipeline` + `_encode_response` under the three settings) | `output_type` switches to "pt"; frames identical across settings; 9-frame window kept; YUV produced only when asked; `X-Post-S` and `post=` present; 17 video frames and an audio stream; `yuv420p` without GPU frames refused |
| Six real LTX clips from GPU session 1 re-encoded both ways (`logs/batch-024/real-clips.txt`, this 8-core machine) | encode 0.41 s (RGB) against 0.23 s (YUV), median; error against the source 0.557 (RGB) and 0.544 (YUV) on 0-255: no loss |
| Local microbenchmark (145 × 352 × 640) | numpy float-to-uint8 0.25 s; `+faststart` costs nothing measurable |
| **On hardware** | **not run**: RUN-14's validation (warm probe and X-03's chain, wall p95 ≤ 0.85 with margin on both shapes; seam and audio checks on the new encode; A/V 0 ms with padding) belongs to GPU session 2 |

## GPU session 2: RUN-14's arms (one warmed box, `vast_run.sh restart` between settings)

1. Default (today): `latency_probe.py --warm 30` for 145 and 153 frames (the reference for this box).
2. `LTX2_GPU_FRAMES=1`.
3. + `LTX2_PIPE_FORMAT=yuv420p`.
4. + `LTX2_X264_THREADS=8`, then `16`.
5. If the box was rented with `VAST_ENV='-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video'` and `/health` reports `encoder: nvenc`: the same probe with h264_nvenc.

For each: wall and server-side ratios (p50/p95/max), `post` and encode times; then `scripts/seam_check.py` and `scripts/audio_check.py` on the chosen setting's clips, and X-03's A/V check. The winning setting goes into `server/server_defaults.sh`; then the seam design's rollout (D-03) and the live run.

## Next

The seam design (LTX-12, AUD-02, AUD-03 as one change, D-03) built and tested for rollout, the live config still on today's settings until GPU session 2.

**Commit:** this batch's.
