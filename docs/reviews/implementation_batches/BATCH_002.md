# BATCH_002 — Runtime baseline file and `bench_report` (RUN-13, part a)

**Status:** COMPLETED (RUN-13 part a; the row stays IN_PROGRESS) · **Date:** 2026-09-22 · **Base:** `b5e6d3d` plus the owner's uncommitted FINAL decision record

## Plan (written before any product code changed)

### Why this batch

Roadmap phase 0 lists, after the timing log, "baseline file, bench_report, JIT simulation test, seam check, audio check, compiler replay harness". That is RUN-13, whose only dependency (RUN-05) completed in BATCH_001. RUN-13 is seven pieces over four different boundaries (a film's timing log, the scheduler under simulation, the browser, the compiler), so it is split:

| Part | Pieces | Batch |
|---|---|---|
| **a** | `benchmarks/runtime/baseline.json`; `scripts/bench_report.py` (reads `timing.jsonl`, prints the gate table, exits non-zero on a failed gate); README table generated from the baseline; CLAUDE.md points at the file instead of restating ×RT figures | **this batch** |
| b | offline JIT simulation test (replay backend, render delay drawn from the baseline, adversarial lines, zero stalls asserted) | next |
| c | `scripts/seam_check.py` (headless Chromium), `scripts/audio_check.py` | later |
| d | offline compiler replay harness; `scripts/latency_probe.py --warm 30` with +3 % tolerance | later |

Part a is one contract: the baseline file's schema and the report that reads a run against it. RUN-13 stays `IN_PROGRESS` until part d.

### Decision IDs

- **RUN-13** (APPROVED; depends on RUN-05, completed). Part a only.
- Not selected: **RUN-04** (APPROVED; depends on RUN-05 and RUN-13, and RUN-13 is not complete). `bench_report` must "fail on a gate" (RUN-13), and the only gate values the record defines are RUN-04's §9.3 table, so the baseline file carries them as the report's input with a citation. RUN-04's own work (declaring them the canonical per-shape gates, and its validation: `bench_report` on the next live run) is left to RUN-04; the ledger notes this.

### Contracts and files likely affected

- `benchmarks/runtime/baseline.json` (new): identity, hardware, recipe, shape, cold/warm, stages, render distributions, Director, film outcomes, seams, and `gates`. Values are review 03's reconstruction of session A (2026-09-18, 62 warm clips), marked as reconstructed; fields review 03 says are unknown are `null` with a note.
- `scripts/bench_report.py` (new): `python scripts/bench_report.py <session dir or timing.jsonl> [--baseline …] [--json]`; exit 0 = no gate failed, 1 = a gate failed, 2 = unreadable input. `--readme` prints the baseline table; `--update-readme` rewrites it between markers in `README.md`.
- `README.md`: the generated table between `<!-- baseline:start -->` / `<!-- baseline:end -->`; the prose number points at it.
- `CLAUDE.md`: the ×RT figures in "Real time was measured…" replaced by a pointer to the baseline and the report; the non-numeric findings stay.
- `tests/test_bench.py` (new).

No product code path changes: nothing in `src/` is expected to change.

### Migrations and compatibility

None. New files; a README section; one CLAUDE.md bullet shortened.

### Risks

- Reconstructed numbers presented as measured. Mitigation: a `provenance` field and a README caption that say how they were obtained.
- Gate semantics drifting from the record. Mitigation: gate names, thresholds and the warm-up rule copied from RUN-04 / review 03 §9.1, §9.3, each cited in the file.
- Gates that the timing log cannot measure (duplicate frames, luminance dip) reported as passing. Mitigation: they are listed as `not measured here (seam_check)`, never PASS.

### Rollback

Delete the new files and revert the README and CLAUDE.md hunks.

### Validation required

- New tests: statistics helpers; a synthetic passing video run; a synthetic run with a stall, a late line and a compile outlier (non-zero exit, the right rows fail); a text-mode run (render gates n/a); a real mock `run_live` log read end to end; the README table equals the generator's output; the baseline file parses and every gate the report evaluates exists.
- `python -m tests.run` green (baseline 32 passed).
- The RUN-13 row's validation for this part: the offline pieces run in `python -m tests.run`.
- `bench_report` on the three BATCH_001 evidence logs (`logs/batch-001/…`), including the real headless run, whose 0.40 s stall must be reported as a failed stall gate.

### Baseline captured

- `python -m tests.run` at session start: **32 passed, 0 failed**.

---

## Result

### Purpose and decision IDs

RUN-13, part a: the canonical runtime baseline as a machine-readable file, a report that checks a run's `timing.jsonl` against its gates, the README table generated from it, and CLAUDE.md pointing at it instead of restating ×RT figures. RUN-13 remains `IN_PROGRESS` (parts b-d below).

### Files changed

| File | Change |
|---|---|
| `benchmarks/runtime/baseline.json` (new) | Schema 1. Session A (2026-09-18, commit `265e9fd`) as reconstructed by review 03: identity, hardware (H100, SXM or NVL; the offer was not recorded), recipe, shape (640×352, 24 fps, 145 frames, overlap 9, 6.0417 s), cold/warm rule (count from the fourth render), stages (null: lost with the box), renders (62 warm: 4.80 s mean, sd 0.046, p95 4.85, max 4.96; ratio p50 0.795, p95 0.802, max 0.820; +2.2 ms per clip), Director (gpt-4.1 priority, n 35, mean 2.98 s, p95 3.80), film (0 stalls, min margin 1.87 s, steering 10.9-14.6 s over 4 lines, first picture 17.9 s), seams, and `gates` (RUN-04's values, cited). A `provenance` field says the numbers are reconstructed and how the next live run replaces them. |
| `scripts/bench_report.py` (new) | `measure()` (render tails as a ratio of each clip's probed length over warm renders; least-squares trend; mid-film compiles = warm renders > 3× the warm median; stalls = consumer waits > 0.05 s after the first clip; minimum queue margin; steering = line received → its clip on air; first picture; Director calls, answering calls, re-asks, fallbacks, cancellations, clips per beat; steady-state swap p95 and reloads from the page's beacons; hold minus the server's clip length). `evaluate()` gives one row per gate: PASS, FAIL, `n/a` with the reason, or `not measured here` for the two seam gates the log cannot see. Text output or `--json`; exit 0 / 1 (a gate failed) / 2 (unreadable input). `--readme` / `--update-readme` generate the README block. Pure Python (no new dependency). |
| `tests/test_bench.py` (new) | 10 tests (below). |
| `README.md` | New "Runtime baseline" section with the generated table between markers; the Latency bullet's "about 4.6 s" (a stale restated figure) now points at the table. |
| `CLAUDE.md` | The "Real time was measured" bullet's ×RT figures replaced by a pointer to the baseline file and the report (the non-numeric findings kept); layout lists `scripts/bench_report.py` and `benchmarks/runtime/`. |
| `docs/ARCHITECTURE.md` | The timing-log paragraph names the report and the baseline. |

No file under `src/` changed.

### Contract

- Gate names in `baseline.json` → the rows `bench_report` evaluates; a gate the report does not know shows as `n/a` with "the report does not know this gate" (tested not to happen with the committed file).
- Warm rule: `cold_warm.warm_after_clips` (3); the stall threshold and the compile factor come from the gates.
- README block markers: `<!-- baseline:start (generated by scripts/bench_report.py --update-readme) -->` … `<!-- baseline:end -->`; a test fails when the block drifts from the file.

### Migrations and compatibility

None.

### Validation

| Check | Command | Result |
|---|---|---|
| New tests | `python -m tests.run test_bench` | 10 passed: statistics helpers; a clean synthetic video run passes every measurable gate and exits 0; a synthetic run with a compile outlier, a slow render, a 0.4 s stall, a negative margin, a late line, a slow swap and a steady-state reload fails exactly those gates and exits 1 with them in `--json`; warm-up clips do not count; lines followed from receipt to the screen, with dropped and never-landed lines counted; a text film leaves the render gates `n/a`; unreadable input exits 2; a real mock `run_live` log is read end to end (clips, landed line, re-ask, clips per beat); the baseline names every gate the report knows; the README block equals the generator's output |
| Full suite | `python -m tests.run` | **42 passed, 0 failed** (`logs/batch-002/tests-final.out`) |
| Static check | pyflakes (scratch install) on the two new Python files | clean |
| RUN-13 part a validation ("the offline pieces run in `python -m tests.run`") | as above | pass |
| Report on real logs | `python scripts/bench_report.py logs/batch-001/<run>/sessions/<id>/` for the three BATCH_001 evidence runs (`logs/batch-002/bench-*.txt`) | real headless run: exit 1, **FAILED: stalls, min_queue_margin_s** (turn 3, 0.40 s held; margin −0.397 s), steering 5.16 s and 4.53 s PASS, first picture 6.0 s PASS, 8 gates n/a (text film, no browser), 2 not measured here. Browser replay run: exit 0, 9 passed (ratio p95 0.485 on replayed clips, swap p95 1.49 ms, 0 reloads), trend n/a (3 warm clips < 150). Terminal loop: exit 0, all n/a (nothing plays in `run_play`). |
| README idempotency | `python scripts/bench_report.py --update-readme` twice | "README.md already current" on the second run |

Benchmarks: none run (no GPU; $0.00). The baseline's numbers are review 03's, not new measurements.

### Deviations from the plan

- The README's Latency bullet carried a restated figure ("about 4.6 s"); it now points at the table. The rest of that bullet (e.g. "not yet in a live session") is stale too but belongs to CLEAN-04's documentation corrections and was left.
- ARCHITECTURE.md's timing-log paragraph now names the report (one sentence).

### Known risks

- The baseline's numbers are reconstructed (review 03) and some fields are unknown (exact card, stage timings); the file says so. RUN-04's validation, a live run through `bench_report`, is what replaces them.
- The two seam gates (duplicate frames, crossfade dip) are `not measured here` until `scripts/seam_check.py` (part c) exists; the current player would fail both by design (the overlap and the crossfade are experiment-gated: LTX-12/AUD-02).
- Runs with a time-scaled test player produce meaningless stall and margin values; the report is for real-time runs.
- `revision` is null for runs on the GPU box (BATCH_001 note), so a live run's identity there needs the shipped hash (a later RUN-13 or RUN-04 step).

### Rollback

Delete `benchmarks/`, `scripts/bench_report.py`, `tests/test_bench.py`; revert the README, CLAUDE.md and ARCHITECTURE.md hunks.

### Unresolved blockers

None for part a. RUN-13 remains open for:

- **b**: the offline JIT simulation test (replay backend, render delay drawn from the baseline, adversarial lines, zero stalls asserted), evaluated with `bench_report.measure`;
- **c**: `scripts/seam_check.py` (promote `logs/_scratch/review-03/dim_*.py`) and `scripts/audio_check.py`;
- **d**: the compiler replay harness and `scripts/latency_probe.py --warm 30` with the +3 % tolerance against the baseline.

### Recommended next batch

RUN-13 part b, the JIT simulation test. It needs a Director delay in the mock backend and a per-clip render delay in the replay backend (test-only knobs), and it should run in seconds at a time scale, so `JIT_MARGIN_S` and the slack must scale with it. It is the harness X-09 and RUN-06 depend on.

### Suggested commit message

```
A runtime baseline file, and a report that checks a run against it

benchmarks/runtime/baseline.json is the canonical render baseline
(session A as reconstructed by review 03, marked as such) with the
RUN-04 gates: render ratio p50/p95/max, trend, mid-film compiles,
stalls, queue margin, steering latency, first picture and the seam
gates. scripts/bench_report.py reads a session's timing.jsonl, prints
one row per gate (PASS, FAIL, n/a with the reason, or not measured
here) and exits non-zero on a failed gate; it also generates the
README's baseline table, and CLAUDE.md now points at the file instead
of restating real-time figures (RUN-13, part a).
```

Commit: not created (not permitted by the loop prompt).
