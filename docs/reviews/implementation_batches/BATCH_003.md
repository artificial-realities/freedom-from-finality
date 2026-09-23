# BATCH_003 — Offline JIT simulation test (RUN-13, part b)

**Status:** COMPLETED (RUN-13 part b; the row stays IN_PROGRESS) · **Date:** 2026-09-22 · **Base:** `e77c787` plus the owner's uncommitted FINAL decision record

## Plan (written before any code changed)

### Why this batch

Phase 0 of the roadmap is still open. The only phase-0 row in progress is RUN-13 (APPROVED; RUN-05 completed), and BATCH_002 split it into parts a-d and recommended part b next. Part b is the offline JIT simulation test: "an offline JIT simulation test in `tests/` (replay backend, render delay drawn from the baseline, adversarial lines, zero stalls asserted)" (record, RUN-13). It is the harness X-09 (and so RUN-06) needs, and the check RUN-03, RUN-07 and RUN-09 name as their validation. The other ready phase-0 candidates (CLEAN-01/02/03/07/09, LTX-08, LTX-03 + CLEAN-08) are separate subsystems and wait for their own batches.

### Decision IDs

- **RUN-13** (APPROVED), part b only. The row stays `IN_PROGRESS` for c (seam and audio checks) and d (compiler replay harness, `latency_probe --warm`).

### What the test is

Review 03 §9.4: "Drive `run_live` with the replay backend and a render delay drawn from the baseline's distribution; inject lines at adversarial times; assert zero stalls, a minimum margin, and latency bounds for the configured policy. It runs in seconds with the mock LLM and catches scheduling regressions in `runner.py`."

- **Policy under test:** `configs/director.live.yaml` as it stands (lead 1, 6 s clips, slack 3.0 s), with the mock LLM and the replay backend swapped in. The runner's `JIT_SAFETY` and `JIT_MARGIN_S` are read from the module.
- **Time:** compressed by a factor k (20). The player's time scale shortens every hold; each draw, `render_slack_seconds` and `JIT_MARGIN_S` are divided by k; the timing log is multiplied back by k before it is measured, so every figure and gate is in real-time seconds. The clip is a real 145-frame, 24 fps MP4 (6.0417 s). The clip-length probe (an ffprobe subprocess of about 30 ms that would count k times) returns the known length.
- **Draws:** render walls and Director calls from `benchmarks/runtime/baseline.json` (render 4.80 ± 0.046 s, max 4.96; Director 2.98 ± 0.59 s, max 4.37, priority tier), as a normal clipped to [mean − 3 sd, min(max, mean + 3 sd)], seeded per (kind, segment, attempt) so a restarted call does not shift later draws. The Showrunner takes 4.5 s (the top of review 01's measured bootstrap range), which also puts a slow background Showrunner in every run.
- **Adversarial lines:** placed from the fires the run actually makes, not from the policy's formula: a line is sent δ = 0.9, 0.5 and 0.2 s before a render fire (the §9.3 "within 1 s before a fire" case), one 0.3 s after a fire, and one in the middle of a Director call (the cancel-and-restart path). Lines are at least two clips apart, so ARCH-06's second-line window (not fixed yet) is not entered.
- **Harness sanity:** a run with the renderer slower than real time must report stalls, so the zero-stall assertion cannot pass vacuously.

### Assertions (current policy)

1. `bench_report` gates on the rescaled log: 0 stalls, first picture ≤ 25 s, no mid-film compile, render ratio p50/p95/max within the gates (sanity of the draws).
2. The steady-state buffer: clips in the middle of a beat with no line are queued `(JIT_SAFETY − 1) × R + JIT_MARGIN_S + render_slack_seconds` before air (± 0.3 s), which is at least the 1.0 s gate.
3. Every line lands; none is dropped; each lands on the first clip emitted after it arrived; a line sent before a fire lands two clips after the clip on air, a line sent after a fire three.
4. A line costs the buffer at most the Director time spent on the segment it lands on; a line sent before a fire delays that fire (it was adversarial) and arrived within 1 s of it.
5. The mid-call line cancels the call it interrupts (a `cancelled` Director record) and lands with the restarted beat.

The minimum queue margin gate (≥ 1.0 s) is **not** asserted under adversarial lines: the current policy spends a line's whole Director call from the ~4.7 s buffer, so a 4.37 s call 0.2 s before a fire leaves about 0.5 s. That is what X-09 and RUN-06 address; the test reports the value.

### Files likely affected

- `tests/jit_sim.py` (new): the harness (draws, the scripted player, the patches, rescaling, measurement through `scripts/bench_report.py`). Not collected by the runner, importable by later tests and X-09.
- `tests/test_jit.py` (new): the tests above.
- `docs/ARCHITECTURE.md` or `CLAUDE.md`: one line naming the test, if a place exists that lists the offline checks.
- Nothing under `src/`: the delays, the probe and the margin constant are patched in the harness with `unittest.mock`, so no test knob enters product code.

### Migrations and compatibility

None.

### Risks

- Timing flakiness on a loaded machine. Mitigation: k = 20 was measured stable (margins within 0.1 s of k = 30); the assertions carry 0.3 s (real-time) tolerances; the prototype's margins were checked over several runs before the assertions were fixed.
- The test depends on the mock Director's beat structure (two-clip beats, a deferred line owed on the next beat). Mitigation: line placement adapts to the beats the run produces, and the assertions read the timing log, not the script.
- The harness patches `runner.JIT_MARGIN_S` and `runner.probe_duration`; a rename breaks the test loudly (the patch fails), not silently.

### Rollback

Delete `tests/jit_sim.py` and `tests/test_jit.py`.

### Validation required

- `python -m tests.run test_jit` passes, repeatedly (five consecutive runs), in a few seconds.
- A deliberate scheduling regression makes it fail (checked by hand, not committed): for example, `JIT_SAFETY` dropped to 0.9, or the fire moved later by the slack.
- `python -m tests.run` green (baseline: 42 passed, `logs/batch-003/tests-baseline.out`).
- pyflakes on the new files.

### Baseline captured

- `python -m tests.run` at session start: **42 passed, 0 failed**.
- Prototype (scratch, not committed) of the live config at k = 10, 20 and 30 with no lines: 0 stalls; mid-beat clips queued 4.71-4.87 s before air; a beat's first clip 5.66-6.03 s (the Director's estimate lead left over); first picture 17.1-17.4 s.

---

## Result

### Purpose and decision IDs

RUN-13, part b: the offline JIT simulation test. It runs the live config's just-in-time schedule against the runtime baseline's render and Director distributions, with viewer lines at adversarial times, and asserts zero stalls, the steady-state buffer, and line landing and cost bounds. RUN-13 remains `IN_PROGRESS` for parts c (seam and audio checks) and d (compiler replay harness, `latency_probe --warm`).

### Files changed

| File | Change |
|---|---|
| `tests/jit_sim.py` (new) | The harness. `simulate(clips, lines, scale=25, seed, config, render, director, showrunner_s)` runs `run_live` on `configs/director.live.yaml` with the mock LLM and the replay backend (a real 145-frame, 24 fps clip). `Spread` holds a delay distribution: a normal clipped to [mean − 3 sd, min(max, mean + 3 sd)], built from the baseline's summaries. Draws are seeded per (kind, segment, attempt). `SimPlayer` records when each clip went on air, when each render fired and each clip's place in its beat. It sends `Line`s at a set time before or after the next render fire that continues a beat (the fire phase is the median of the run's own earlier fires), or partway through a Director call that is not answering a line. It sends a line only after the previous one has played. Through `unittest.mock` the harness patches `MockBackend.generate_structured` (Director delay), `MockBackend.showrunner_delay`, `ReplayI2V.synthesize` (render delay, fire time), `runner.JIT_MARGIN_S` (÷ scale) and `runner.probe_duration` (the known length; ffprobe's ~30 ms would count 25 times). It divides `render_slack_seconds` by the scale, multiplies the log's time fields back into real-time seconds, and measures the result with `scripts/bench_report.py` (`measure`, `evaluate`). |
| `tests/test_jit.py` (new) | Three tests: the draws are clipped and reproducible; the live schedule under adversarial lines; a renderer slower than real time is reported as stalling. |
| `docs/ARCHITECTURE.md` | The testing paragraph names `tests/test_jit.py` and what it checks. |
| `CLAUDE.md` | The layout's `tests/` line names `jit_sim.py`. |

No file under `src/` changed: the delays are injected from the test side, so no test knob entered product code (the BATCH_002 recommendation suggested knobs in the mock and replay backends; patching was chosen instead).

### Behaviour and contract

- **Policy under test:** `configs/director.live.yaml` as it stands (lead 1, 6 s clips, slack 3.0 s, priority-tier Director), `JIT_SAFETY` and `JIT_MARGIN_S` from `src/eval/runner.py`. Changing either the config or the constants changes what the test checks. A policy change that moves lines (RUN-06's deadline-aware admission) or adds an explicit buffer (RUN-03's `buffer_seconds`) has to update the corresponding assertion. That is intended.
- **The adversarial run (34 clips, ~9 s wall):** one line 60 % of the way through a beat-end Director call, three lines 0.9, 0.6 and 0.3 s before a render fire, and one 0.3 s after a fire. Assertions:
  1. `bench_report` gates PASS: stalls, first picture, mid-film compiles, steering p50/p95, render ratio p50/p95/max.
  2. Before the first line, mid-beat clips are queued `(JIT_SAFETY − 1) × mean render + JIT_MARGIN_S + render_slack_seconds` (≈ 4.7 s) before air, ± 0.3 s, and that buffer clears the 1 s gate.
  3. Every line is sent and lands; none is dropped; each lands on the first clip emitted after it arrived.
  4. A line's landing clip keeps at least `buffer − Director time spent on that segment` (± 0.3 s).
  5. Before-fire lines land two clips after the one on air. The fire waited for the answer. The buffer the line spent puts its arrival within 0.3 s of the intended 0.9 / 0.6 / 0.3 s before the fire, which shows the line was adversarial.
  6. The after-fire line lands three clips after the one on air and costs nothing.
  7. The mid-call line cancels the call it interrupts, and the restarted call answers it.
- **Not asserted:** the minimum queue margin gate (≥ 1.0 s) under adversarial lines. The current policy spends a line's whole Director call from the ~4.7 s buffer. In the recorded run the 0.3 s-before-fire line, answered by a 3.96 s call, leaves 0.95-0.99 s, which fails that gate by design. X-09 and RUN-06 exist to bound this. The harness is ready for them: pass a different `director` spread (the default tier), `render_slack_seconds` through a config, or more lines.

### Migrations and compatibility

None.

### Validation

| Check | Command | Result |
|---|---|---|
| New tests | `python -m tests.run test_jit` | 3 passed |
| Repeatability | the same, five consecutive runs (`logs/batch-003/test_jit-repeat.txt`) | 5 × 3 passed, 12.85-12.95 s each |
| Under load | three concurrent copies with two, then eight, busy CPU loops on the 8-core box | all 3 × 3 passed both times |
| Catches regressions | `logs/batch-003/tools/mutate.py` applies one mutation to `src/eval/runner.py`, runs `test_jit` and restores the file (`git diff` clean afterwards). Output: `logs/batch-003/mutations.txt` | all five caught by the adversarial test: `JIT_SAFETY` 0.9 → 1 stall (0.81 s); slack dropped from the fire time → 4 stalls (2.69 s); fire one clip early → steering p50 18.5 s; a line no longer cuts the beat → steering p50 13.9 s; the render in flight no longer takes the next slot → steering p50 18.6 s |
| Full suite | `python -m tests.run` (`logs/batch-003/tests-final.out`) | **45 passed, 0 failed** (baseline 42); 21.6 s wall (`logs/batch-003/suite-time.txt`) |
| Static check | pyflakes (scratch venv) on `tests/jit_sim.py`, `tests/test_jit.py` | clean (after removing one unused import) |
| RUN-13 row validation for this part ("the offline pieces run in `python -m tests.run`") | as above | pass |

One recorded adversarial run, per clip (`logs/batch-003/adversarial-run.txt`, driver `logs/batch-003/tools/dump.py 34 25`): 0 stalls; first picture 16.9 s; mid-beat margins before the first line 4.69-4.78 s; lines landed at 11, 15, 21, 27, 32; landing margins 3.42 s (mid-call), 1.84, 1.80 and 0.99 s (before-fire, answered in 3.77, 3.34 and 3.96 s), 4.59 s (after-fire); steering 11.2, 10.5, 10.2, 9.9 and 15.3 s.

Benchmarks: none run (no GPU; $0.00). The simulation draws from the baseline and does not measure hardware.

### Deviations from the plan

- The planned scale was 20 and the run length about 20 clips. Line placement adapts to the mock's beats (a two-clip answer, then a one-clip beat for the deferred request with a re-ask), so five lines need 34 clips. The scale is 25: measured stable, and the module stays near 13 s.
- The δ values are 0.9, 0.6 and 0.3 s rather than 0.9, 0.5 and 0.2 s, which keeps the smallest one clear of timer jitter at 25×. All three are still inside §9.3's "within 1 s before a fire".
- One observation shaped the assertions. A late fire delays the next clip's render as well, because renders are drained in order: the clip after a line's landing clip is queued `landing margin + (L − R)` before air. The steady-buffer check therefore uses only clips before the first line.

### Known risks

- **Timing:** this is a wall-clock test at 25×, so 10 ms of stall on the machine is 0.25 s of film time. It passed under full CPU saturation, and the margins it depends on are 0.9 s (real-time) or more, i.e. 36 ms scaled.
- **Mock coupling:** the run's beat structure comes from the mock Director's script. A script change can move where lines are sent, or leave one unsent. The test then fails with the unsent line named. It does not pass silently.
- **Private names:** the harness patches `runner.JIT_MARGIN_S`, `runner.probe_duration` and imports `_segment_number` / `_viewer_line` from the mock. A rename breaks the harness loudly.
- **Scope:** the test covers the pipelined render path only; the serial TTS path is AUD-04's to remove. It does not cover ARCH-06's second-line window: lines are two or more clips apart, and a test of that window belongs to ARCH-06.

### Rollback

Delete `tests/jit_sim.py` and `tests/test_jit.py`; revert the one-line hunks in `docs/ARCHITECTURE.md` and `CLAUDE.md`.

### Unresolved blockers

None for part b. RUN-13 remains open for:

- **c**: `scripts/seam_check.py` (promote `logs/_scratch/review-03/dim_*.py`; headless Chromium) and `scripts/audio_check.py`;
- **d**: the offline compiler replay harness and `scripts/latency_probe.py --warm 30` with the +3 % tolerance.

### Recommended next batch

RUN-13 part c or d, to finish the row. Part d's compiler replay harness is the offline prerequisite of X-05 and the LTX-01 arm. Part c's `seam_check.py` is X-02's prerequisite and needs the Playwright environment under `logs/_scratch/`. If RUN-13 is deferred, the mechanical phase-0 cleanup batch (CLEAN-01/02/03/07/09, LTX-08) is ready, and ARCH-06 (the second viewer line) is now directly testable with `tests/jit_sim.py`.

### Suggested commit message

```
An offline simulation of the just-in-time schedule

tests/jit_sim.py runs configs/director.live.yaml with the mock Director
and replayed clips 25 times faster than real time, with render walls
and Director calls drawn from benchmarks/runtime/baseline.json, and
scales the timing log back so bench_report judges it in real seconds.
Viewer lines are sent just before and just after a render fire and in
the middle of a Director call, placed from the fires the run makes.

tests/test_jit.py asserts what the current policy promises: no stall,
the steady-state buffer the schedule is built to keep, every line on
the earliest clip not yet rendering at no more than its Director time
out of the buffer, and a stall reported when the renderer is slower
than real time (RUN-13, part b).
```

Commit: not created (not permitted by the loop prompt).
