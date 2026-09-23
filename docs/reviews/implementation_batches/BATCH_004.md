# BATCH_004 — Seam check and audio check (RUN-13, part c)

**Status:** COMPLETED (RUN-13 part c; the row stays IN_PROGRESS for part d) · **Date:** 2026-09-22 · **Base:** `a14c44a` plus the owner's uncommitted FINAL decision record

## Plan (written before any code changed)

### Why this batch

Phase 0 is still open; the only phase-0 row in progress is RUN-13 (APPROVED, RUN-05 done). BATCH_003 left parts c and d and recommended either. Part c is the pair of offline checks the record names for the seam work: "`scripts/seam_check.py` (headless Chromium) after player changes; `scripts/audio_check.py` after audio changes" (RUN-13), listed in §5 item 14 as the infrastructure X-02 and X-13 need. Everything it needs is on this machine: the 63 clips of the 2026-09-18 run under `logs/live-box/video/`, ffmpeg 4.4.2, numpy, and the Playwright Chromium under `logs/_scratch/pwenv` (the memory note's setup). Part d's `latency_probe --warm` needs a GPU to validate, so c goes first.

### Decision IDs

- **RUN-13** (APPROVED), part c only. The row stays `IN_PROGRESS` for part d (the compiler replay harness, `latency_probe.py --warm 30` with the +3 % tolerance).

Nothing here changes player, server or audio behaviour. The checks measure; they approve nothing about LTX-12, AUD-02, AUD-03 or RUN-12 (record §5: "approving them approves nothing about the rows they test").

### What the two scripts measure

**`scripts/seam_check.py`** — the seam gates of `benchmarks/runtime/baseline.json` that `bench_report` marks "not measured here": `duplicate_frames_per_seam` and `crossfade_dip`, plus `swap_p95_ms` and `reloads_on_playing` from the page's own beacons.

1. *Duplicate frames per seam* (files only, no browser). For each consecutive pair of clips, the last 16 frames of N and the first 16 of N+1 (grey, 160×96) give an error matrix; the alignment score for an overlap of k frames is the mean error along the diagonal that maps N+1's first k frames onto N's last k. A valley at k, deeper than both neighbours by 20 % of its own value, is a k-frame overlap; a monotone rise from k = 1 is a continuation (0); a flat profile is "undetermined" (no motion: the frames are the same either way and a viewer sees no hold). Calibrated on the 62 seams of the 2026-09-18 run before this plan was written: every seam with visible motion (48 of 62) reads exactly 9; the other 14 are static seams whose valley is under 0.1 and which the rule reports as undetermined. The method cannot resolve a single duplicated frame (an overlap of 1 reads as a continuation); the gate targets the nine-frame hold. The displayed count is the file overlap minus `--hidden-frames`, the frames a player skips at the seam (0 for today's player; LTX-12's candidate player would pass its overlap).
2. *Crossfade luminance dip, swap latency, reloads on `playing`* (headless Chromium). The script starts the real `WebPlayer` in-process with the clips, feeds them at controlled queue timings that reproduce the live schedule's three cases (review 03 §3: a mid-beat clip queued 1.3 s after the previous went on air; a beat's first clip queued 0.06 s after, inside the crossfade; two clips queued at once), and drives the page with Playwright from a second interpreter (`--browser-python`, because the Playwright venv has no numpy and the project venv has no Playwright). In the page, a `requestAnimationFrame` loop records, per frame, each video element's computed opacity, turn, media time and readiness, and the luminance of the composite drawn on a small canvas exactly as the CSS composites it (black stage, then each element with its opacity). The dip at a seam is `1 − min luminance within ±0.3 s of the switch / min(median luminance 0.3–1.0 s before, 0.3–1.0 s after)`. Swap latency and reloads come from the `/beacon` route through a `TimingLog`, so `bench_report.measure` reads them exactly as it reads a film's. One screenshot-based cross-check of the canvas luminance is run by hand in this session and recorded below.

**`scripts/audio_check.py`** — the automatable rows of review 04 §9.1 that need only ffmpeg and numpy: per clip integrated loudness and true peak (`ebur128`), audio-shorter-than-video, leading and trailing digital silence; then the *played mix* (the offline reproduction of what the player outputs, review 04 §9.1) with a selectable seam policy — `today` (each clip starts at the previous clip's video end with a 250 ms linear fade-in, as `player.html` does now) or `overlap` (the AUD-02 candidate for X-02/X-13: the next clip starts `--overlap` seconds before the previous one's end with an equal-power crossfade; off by default, an evaluation option only) — measured for the seam level step (100 ms each side of the transition; median and p90), onset clicks (a sample step ≥ −30 dBFS in the 5 ms after a join), digital-silence holes (≥ 200 ms below −70 dBFS; count and per minute), the narrated-to-next loudness step when `events.jsonl` is given (voiceover clips), clips above the ceiling and true-peak overs. Thresholds are the record's X-02 criteria and review 04 §9.1, kept in the script and printed as PASS/FAIL; they are not added to `baseline.json`, whose `gates` are RUN-04's list. Not included: ASR word recall, narration rate and speaker cosine, which need faster-whisper and Resemblyzer (the review's scratch venv, not a project dependency); they stay X-01/X-13/X-15 tooling and the script says so.

### Files likely affected

- `scripts/seam_check.py` (new), `scripts/audio_check.py` (new).
- `tests/test_checks.py` (new): the measurement functions on synthetic material — frame arrays and ffmpeg-made clips with a known overlap (`testsrc`, cut with and without a 9-frame overlap), a dip series, synthetic tone/silence clips for loudness, steps, clicks and holes. No browser in the suite.
- `docs/ARCHITECTURE.md` (testing paragraph), `CLAUDE.md` (layout lines), `README.md` only if it lists the checks (it does not).
- Nothing under `src/`, `server/`, `configs/`, `stories/`.

### Migrations and compatibility

None.

### Risks

- The overlap estimator is undetermined on static seams; the summary reports how many seams were determined, and the test uses material with motion.
- The browser half depends on Playwright being installed for `--browser-python`; the script refuses clearly when it is not, and the file-level part still runs.
- The canvas composite models the CSS compositing rather than photographing it; the one-off screenshot cross-check bounds the difference.
- `ebur128` parsing depends on ffmpeg's log format; the test asserts a known loudness within 1 LU.

### Rollback

Delete the two scripts and the test; revert the doc hunks.

### Validation required

- `python -m tests.run test_checks` passes, repeatedly.
- `python -m tests.run` green (baseline 45 passed, `logs/batch-004/tests-baseline.out`).
- pyflakes on the new files.
- `scripts/seam_check.py` on the 2026-09-18 clips, files only and with the browser: today's player should show the nine-frame overlap, a dip near 24 % (review 03 §3), and reloads on the inside-the-crossfade and two-queued cases; swap p95 within the gate.
- `scripts/audio_check.py` on the 2026-09-18 clips with its `events.jsonl`: numbers in the range review 04 measured (16/63 above −18 LUFS, 14/63 over −1 dBTP, narrated-to-next median ≈ 20.8 LU, 37 clips ending in digital silence, seam steps far above 12 dB).

### Baseline captured

- `python -m tests.run` at session start: **45 passed, 0 failed** (`logs/batch-004/tests-baseline.out`).
- Estimator calibration on the 62 real seams (before writing the script): 48 read 9 with a clear valley, 14 static seams flat.

---

## Result

### Purpose and decision IDs

RUN-13, part c: the two offline seam tools. `scripts/seam_check.py` measures the seam gates of `benchmarks/runtime/baseline.json` that `bench_report` cannot read from the timing log, from a run's clip files and in headless Chromium; `scripts/audio_check.py` measures the audio criteria of X-02 and review 04 §9.1 on the clips and on the played mix. RUN-13 remains `IN_PROGRESS` for part d (the compiler replay harness; `latency_probe.py --warm 30` with the +3 % tolerance). No player, server, audio or story behaviour changed; nothing under `src/`, `server/`, `configs/` or `stories/` was touched.

### Files changed

| File | Change |
|---|---|
| `scripts/seam_check.py` (new) | **Files:** `seams_from_files` decodes each clip to grey 160×96, builds the 16×16 block-error matrix at every seam (`block_error`: the 99th-percentile 8×8 block of the mean absolute difference, so local motion counts and decoder noise does not), takes the alignment profile over overlaps 1-15 (`overlap_profile`) and reads it (`estimate_overlap`): a valley at k ≥ 2 deeper than both neighbours by 20 % of itself is a k-frame overlap; a profile lowest at k = 1 and rising from there is a continuation (0); anything else is undetermined. `--hidden-frames` gives the displayed count. **Browser:** `run_browser` starts the real `WebPlayer` in-process with a `TimingLog` on its `/beacon` route, launches the page half under `--browser-python` (`browser_page`, Playwright only, stdlib imports), waits for the page, then plays the clips at the queue timings of `--cases` (A: the next clip queued 1.3 s after the current went on air; B: 0.06 s after; C: two clips before, at +3.0 s, so two are queued). The page's `requestAnimationFrame` sampler (`SAMPLER_JS`) records per frame each video element's opacity, turn, media time, readiness and active flag, and the luminance of the composite drawn on a 64×36 canvas the way the CSS composites it. `dips_from_samples` finds each switch (the active element's turn changes), the dip (1 − min within ±0.3 s over the darker median of 0.3-1.0 s before and after), when it bottomed, and whether the outgoing element was replaced while still visible (readiness 0 or another turn at opacity > 0.05). `bench_report.measure` reads the swap and reload beacons. `gate_rows` judges the four gates; exit 1 on a FAIL. Output: `<out>/seam_check.json`, `samples.json`, `timing.jsonl`, `marks.json`. |
| `scripts/audio_check.py` (new) | Per clip: `ebur128` (integrated LUFS and true peak from ffmpeg's summary; silence gives −70 and −inf), `pcm`, `silence_edges`, the audio-shorter-than-video gap. `mix` builds the played mix under `today` (each clip at the previous clip's video end with a 250 ms linear fade-in) or `overlap` (the AUD-02 candidate: the next clip `--overlap` s before the previous ends, equal-power crossfade; evaluation only). `seam_metrics`: the level step 100 ms each side of the transition and the jump at the join sample, a click when ≥ −30 dBFS and ≥ 4× the median sample step of the next 20 ms. `holes`: runs ≥ 200 ms below −70 dBFS. `check` adds the narrated-to-next step from `events.jsonl`, clips above `--ceiling` (−18 LUFS, X-13's candidate) and true-peak overs, and `criteria_rows` judges the seven criteria with their sources; exit 1 on a FAIL. Output: `<out>/audio_check.json`. |
| `tests/test_checks.py` (new) | Eight tests on synthetic material with known answers: the estimator on sliding-block frames with fresh noise in the next clip (overlaps 9 and 3, a continuation, static both ways); the same through H.264 files written from numpy frames, with `--hidden-frames`; `find_clips`; `dips_from_samples` on a 60 Hz series with a 25 % parabola, none, an outgoing element reloaded mid-fade, and too few samples; `gate_rows`; `ebur128`/`silence_edges` on a −20 dBFS tone, silence and a 0.95 cosine; the three mix policies (steps, a click only at a hard join from silence into the cosine, holes); `check` end to end with and without an events file. |
| `docs/ARCHITECTURE.md` | The timing-log paragraph names the two scripts as the owners of the gates the log cannot carry; the testing paragraph names `tests/test_checks.py` and what the two scripts measure. |
| `CLAUDE.md` | Layout: the `tests/` line names `test_checks`; two lines for the scripts. |

### Behaviour and contract

- Both scripts are measurement only. `seam_check.py` reads the four seam gates' thresholds from `baseline.json` (`duplicate_frames_per_seam`, `crossfade_dip`, `swap_p95_ms`, `reloads_on_playing`); `audio_check.py` keeps its seven criteria in `CRITERIA` with their sources and does not extend `baseline.json`'s gates (RUN-04's list).
- The seam check's browser half depends on the page contract only through the DOM the sampler reads (`.stage video`, `dataset.turn`, `.active`, `#watch`) and the `/beacon` route. A player change that renames those breaks the check loudly.
- `--policy overlap` in the audio check is an off-by-default evaluation option for X-02/X-13; the player still fades in.
- The overlap estimator resolves the nine-frame hold; a single duplicated frame reads as a continuation (documented in the script).

### Migrations and compatibility

None.

### Validation

| Check | Command | Result |
|---|---|---|
| New tests | `python -m tests.run test_checks` | 8 passed; three runs 2.32-2.37 s (`logs/batch-004/test_checks-repeat.txt`) |
| Full suite | `python -m tests.run` (`logs/batch-004/tests-final.out`) | **53 passed, 0 failed** (baseline 45); 24.3 s wall (`suite-time.txt`) |
| Static check | pyflakes (scratch venv) on the three new files | clean |
| Seam check, files only | `python scripts/seam_check.py logs/live-box/video --run 20260918 --clips 12 --no-browser` (`logs/batch-004/seam-files-only/`) | clips 1-12: 4 of 11 seams determined (the rest static), all 9 → duplicate frames per seam 9, FAIL as expected. On all 62 seams during calibration: 48 determined, all 9. |
| Seam check with the browser | the same with `--browser-python logs/_scratch/pwenv/bin/python` under `LD_LIBRARY_PATH=$PWD/logs/_scratch/libs/lib` (`logs/batch-004/seam-browser-2/`) | Chromium 151, 11 of 11 switches sampled at 16.7 ms. **Dip median 24.9 %** (review 03 §3: 24 %), bottoming 67-83 ms after the switch. **Swap p95 2.1 ms** (review: 1-25 ms), PASS. **Reloads on `playing` 6** of the 9 counted swaps, at turns 4, 5, 7, 8, 10, 11: the clip before each C-case clip and the C clip itself, review 03 §3 2c ("the second overwrites the first"), each a 10-13 ms swap against 1.2-2.2 ms prefetched. **Outgoing element replaced during the fade at 3 seams** (2→3, 5→6, 8→9): exactly the fades during which a B-case clip was queued 0.06 s after the switch (review 03 §3 2d, "the fading element is emptied and reloaded at opacity 0.85"), visible as one-frame blinks of 85 % and 71 % at two of them (the third was replaced between samples with a near-identical frame). The review had confirmed the mechanism but not its magnitude; it is now measured. |
| Canvas against screenshots | six clips, A-case only, page half replaced by `logs/batch-004/tools/page_shots.py` (screenshots of the picture's centre every 34 ms plus the sampler); `compare_shots.py` (`logs/batch-004/screenshot-crosscheck.txt`) | dips 24.6-25.0 % from screenshots against 24.9 % from the canvas at all five seams |
| Audio check, today's player | `python scripts/audio_check.py logs/live-box/video --run 20260918 --events .../events.jsonl` (`logs/batch-004/audio-today/`) | 63 clips, 9.3 s: **16 above −18 LUFS** (review 04: 16/63), **14 over −1 dBTP** (14/63), **37 end in ≥ 0.2 s of digital silence** (37), **narrated-to-next median 20.8 LU** (20.8), audio shorter than video by 25.7 ms median (the review's 32 ms compared container durations; this is decoded samples against the video), seam step median 21.5 dB / p90 59 dB, clicks 0, holes 162 (25.5 per minute). |
| Audio check, detectors exercised | `--fade-in 0` (`audio-nofade/`); `--policy overlap` (`audio-overlap/`) | At the raw join: step median 36.4 dB (the review's "median 36 dB") and 5 clicks (the review's 12 used a different definition). Under the overlap candidate: 0 clicks, step median 26.8 dB: the clips' silent tails remain, which is AUD-03's padding and ceiling, the other half of that design. |
| RUN-13 row validation for this part ("the offline pieces run in `python -m tests.run`") | as above | pass |

Benchmarks: none run (no GPU; $0.00).

### Deviations from the plan

- The click definition was tightened after the first real run flagged loud continuous audio as clicks: a click is now a jump at the join sample that is both ≥ −30 dBFS and at least four times the signal's own sample steps.
- The replaced-during-fade flag was rewritten after the first browser run caught the 2d mechanism but did not flag it: it is keyed on the outgoing element's index, not its turn (the element already carried the new turn).
- The estimator's continuation rule reads a rise over the first three overlaps instead of strict monotonicity, and the synthetic file test uses numpy frames encoded with ffmpeg rather than `testsrc`, whose flipping digits saturate the block metric.
- The test material gives the next clip its own noise, so duplicated frames are near, not identical, as after LTX's VAE round trip.

### Known risks

- `run_browser` waits on `WebPlayer._clients` (private) for the page to connect, as the review's scratch drivers did; a rename fails loudly.
- The canvas composite models the CSS compositing; the screenshot cross-check bounds the difference at 0.3 % on today's page. A player that composites differently (MSE, canvas) needs the cross-check repeated once.
- The audio check's `holes_per_minute` criterion is review 04's room-tone-on target; while AUD-09 is deferred it fails by design and is informative.
- ASR recall, narration rate and speaker cosine are not measured (heavy dependencies); they remain X-01/X-13/X-15 tooling.

### Rollback

Delete `scripts/seam_check.py`, `scripts/audio_check.py`, `tests/test_checks.py`; revert the hunks in `docs/ARCHITECTURE.md` and `CLAUDE.md`.

### Unresolved blockers

None for part c. RUN-13 remains open for part d: the offline compiler replay harness (a transcript's per-clip fields through the current compiler, and through the LTX-01 arm once it exists) and `scripts/latency_probe.py --warm 30` with the +3 % tolerance (GPU-validated only).

### Recommended next batch

RUN-13 part d, to close the row: the compiler replay harness is the offline prerequisite of X-05 and of the LTX-01 arm, and the probe's `--warm` mode can be built now and validated on the first authorised GPU session. Otherwise the mechanical phase-0 cleanup batch (CLEAN-01/02/03/07/09, LTX-08) or ARCH-06.

### Suggested commit message

```
A seam check and an audio check, offline, for the seam work

scripts/seam_check.py measures the seam gates bench_report cannot read
from the timing log: duplicate frames per seam from a run's clips (an
alignment profile over the last and first sixteen frames; a valley is an
overlap, a rise a continuation, a flat profile undetermined), and in
headless Chromium, with the real WebPlayer feeding clips at the live
schedule's queue timings, the crossfade luminance dip from a canvas
composite of the page's elements, the swap latency and the reloads on
playing from the page's own beacons, plus an outgoing element replaced
while still visible.

scripts/audio_check.py measures per-clip loudness and true peak and the
played mix's seam level step, onset clicks, digital-silence holes and
narrated-to-next step, under the player's fade-in or a candidate overlap
crossfade. tests/test_checks.py pins both on synthetic clips with known
answers (RUN-13, part c). On the 2026-09-18 run they reproduce reviews
03 and 04: a 24.9 % dip, six reloads when two clips are queued, an 85 %
one-frame blink when a clip is queued inside the fade, 16 clips above
the ceiling, a 20.8 LU narrated-to-next step.
```

Commit: not created (not permitted by the loop prompt).
