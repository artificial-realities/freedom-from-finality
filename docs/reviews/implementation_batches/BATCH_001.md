# BATCH_001 — Timing log and browser beacons (RUN-05)

**Status:** COMPLETED · **Date:** 2026-09-22 · **Base:** HEAD `42ddda8` plus the owner's uncommitted FINAL decision record

## Plan (written before any product code changed)

### Why this batch

Record §2 puts phase 0 ("measure and clean") first and says nothing in phase 3 or later may start before the phase-0 measurement lands, because every gate reads the timing log. RUN-05 is the first row of phase 0, has no dependencies, and RUN-13, RUN-04, RUN-06, RUN-02, LTX-15 and X-07/X-08/X-09 all depend on it. It is one contract (what a run records about its own timing) across one boundary (the live loop, its player and its backends), so it is a batch on its own.

### Decision IDs

- **RUN-05** (APPROVED, ARCH infrastructure). Recommended choice: `logs/sessions/<id>/timing.jsonl` with the selected shape; fire, render_done (server headers), enqueued, playing, hold_end; line received and landed; Director start, end, clip count, retry; browser swap, reload-on-playing and waiting via a `/beacon` route. No behaviour change. Validation: a headless run writes the file.
- Dependencies: none.

### Contracts and files likely affected

- `src/util/timing_log.py` (new): an append-only JSON-lines writer; a log with no path writes nothing.
- `src/eval/runner.py`: `run_live` writes session, clip, render, queue, playback and line records; `run_play` writes the subset that exists in a turn-by-turn loop.
- `src/graph/director_engine.py` + `src/graph/__init__.py`: the engine writes `director_start` / `director_end` (clip count, retry, fallback, cancelled, bootstrap) around its plan, because the retry and the Director-only time are known only there.
- `src/agents/director.py`: `run` also reports whether the validator re-ask happened (internal return value; its only caller is the engine).
- `src/playback/player.py`: line ids and an `on_line` observer on the base `Player`; an `on_beacon` observer the web player calls.
- `src/i2v/base.py`, `src/i2v/ltxv.py`, `src/i2v/replay.py`: `last_render`, the server's per-clip headers (`X-Latency-S`, `X-Encode-S`, `X-Timings`, `X-Num-Frames`, …) for the `render_done` record.
- `src/web/server.py`: `POST /beacon` (whitelisted kinds, numeric fields only, small bodies).
- `src/web/player.html`: `swap`, `loadstart_on_playing` and `waiting` beacons; nothing on screen changes.
- `tests/test_timing.py` (new).
- Docs: `docs/ARCHITECTURE.md` / `CLAUDE.md` mention of the new file, only as far as the layout lists need it.

"Selected shape" is recorded as it exists today (requested seconds at `fire`; frames, fps and delivered seconds from the server's headers at `render_done`). `ClipShape` is RUN-01's and is not introduced here.

### Migrations and compatibility

None. A new file per session; no schema, config or state change. Old sessions simply have no `timing.jsonl`.

### Risks

- Accidental behaviour change in the scheduler (the records sit inside `_listen`, `_plan`, the consumer). Mitigation: records are writes only; no await added; existing timing tests must stay green unchanged.
- Log volume: about ten records per clip, flushed per line. Negligible against a 6 s clip.
- Browser beacons add POSTs from the page. `sendBeacon` is fire-and-forget; failures are swallowed.

### Rollback

Revert the batch's diff; nothing else reads the new file yet.

### Validation required

- `python -m tests.run` green (baseline: 25 passed, 0 failed).
- New targeted tests: log writer, line ids, records of a mock live run in text mode and with replayed video, `/beacon` route (accepts, rejects, lands in the log), Director retry flag.
- The row's own validation: a headless run writes `logs/sessions/<id>/timing.jsonl`.
- Because `player.html` changes: a headless-Chromium run of the replay player to confirm the page still plays and the beacons arrive (if the local Chromium setup still works; otherwise recorded as not run).

### Baseline captured

- `python -m tests.run` on the untouched tree: **25 passed, 0 failed** (9.2 s).

---

## Result

### Purpose and decision IDs

RUN-05 only: every run writes `logs/sessions/<id>/timing.jsonl`, and the web page reports its own swap timing to it. Nothing reads the file during a run; no scheduling, story or rendering behaviour changed.

### Files changed

| File | Change |
|---|---|
| `src/util/timing_log.py` (new) | `TimingLog`: append-only JSON lines, each with monotonic `t`, Unix `wall` and `event`; flushed per line; never raises (an unopenable or failing log switches itself off with one warning); event fields cannot overwrite `t`/`wall`/`event`. `git_revision()` → (commit, dirty) or (None, None) without a checkout. |
| `src/eval/runner.py` | `run_live` and `run_play` open the log in the session directory and pass it to the engine. `run_live` records `session_start`, per clip `emit` / `fire` / `render_done` / `enqueued` / `playing` / `hold_end`, per line `line_received` / `line_taken` / `line_landed` / `line_dropped`, browser `beacon`s, `error` and `session_end`. `run_play` records the subset a turn-by-turn loop has. The loop tracks which line ids each plan carries (`_take`, `_abort`); the on-air turn is kept in `air["turn"]`. |
| `src/graph/director_engine.py`, `src/graph/__init__.py` | `DirectorEngine(timing=…)`. `plan()` writes `director_start` / `director_end` around the unchanged plan body (now `_plan`), including cancelled and failed calls; `_plan` returns `beat`, `clips`, `director_s`, `retry`, `fallback`, `bootstrap`. |
| `src/agents/director.py` | `run` returns `(beat, notes, retried)`; the engine is its only caller. |
| `src/playback/player.py` | Lines get ids as they arrive; `taken` holds the ids the last `next_input` returned; observers `on_line(id, text)` and `on_beacon(record)`. `next_input` returns the same joined text as before. |
| `src/i2v/base.py`, `ltxv.py`, `replay.py` | `last_render`: the server's `X-*` headers (typed by `render_headers`), requested frames, whether the reference was sent or re-sent, HTTP status; the replay backend reports its source file and delay. `LtxvI2V.last_latency_s` is now derived from `last_render` (one source). |
| `src/web/server.py` | `POST /beacon`: kinds `swap`, `loadstart_on_playing`, `waiting`; numeric fields `turn`, `ms`, `media_time`, `offset` and flags `armed`, `prefetched` pass; anything else is dropped; bodies over 2 KB → 413; bad JSON or kind → 400; 204 otherwise. |
| `src/web/player.html` | `beacon()` via `navigator.sendBeacon` (fetch keepalive fallback); `swap` (ms from the SSE `playing` event to the element's `playing`, with `prefetched`, `offset`, `armed`), `loadstart_on_playing` (the clip was not prefetched), `waiting` (the active element stalled). Nothing on screen changes. |
| `tests/test_timing.py` (new) | 7 tests (below). |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | The timing log and `/beacon` where run outputs and routes are described. |

### Record contract (as implemented)

| Event | Fields |
|---|---|
| `session_start` | `session`, `loop` (`live`/`play`), `config`, `story`, `revision`, `dirty`, `llm_backend`, `director_model`, `director_service_tier`, `video`, `i2v_backend`, `clip_seconds`; live adds `lead_clips`, `queue_size`, `render_slack_seconds`, `jit_safety`, `jit_margin`, `time_scale` |
| `emit` | `turn`, `beat`, `clip`, `of`, `seconds`, `voiceover` |
| `fire` | `turn`, `seconds` (requested clip length), `due_in` (seconds until due on air; null while the lead builds) |
| `render_done` | `turn`, `ok`, `render_s`, `server` (ltxv: `requested_frames`, `reference`, `reseeded`?, `status`, `latency_s`, `encode_s`, `timings`, `num_frames`, `duration_s`, `frame_rate`, `has_audio`, `variant`, `encoder`; replay: `source`, `delay`) |
| `enqueued` | `turn`, `duration` (probed media seconds), `queued`, `margin` (seconds before air; negative = the picture already held; null during the lead) |
| `playing` | `turn`, `duration`, `hold`, `waited` (consumer wait after the previous hold: a stall when > 0.05 s), `first` |
| `hold_end` | `turn` |
| `line_received` | `line` (id), `on_air`, `into` (seconds into the on-air clip), `chars`, `sha` (12 hex of SHA-1) |
| `line_taken` | `turn` (segment the plan starts at), `lines` |
| `line_landed` / `line_dropped` | `turn`, `lines` (+ `reason` for dropped) |
| `director_start` | `turn`, `input` |
| `director_end` | `turn`, `input`, `plan_s` (whole plan, bootstrap included), then either `beat`, `clips`, `director_s`, `retry`, `fallback`, `bootstrap`, or `cancelled` (+ `error`) |
| `beacon` | `kind` + the passed fields |
| `error`, `session_end` | `where`, `turn`, `error` · `turn`, `played` (live) |

"Selected shape" is recorded as it exists before RUN-01: requested seconds at `fire`, and the server's frame count, fps and delivered seconds in `render_done.server`. RUN-01's `ClipShape` should replace `seconds` with the shape's fields.

### Migrations and compatibility

None. A new file per session. The only signature changes are internal (`director_agent.run`'s return; `build_graph`/`DirectorEngine` gain an optional `timing`). Old session directories have no `timing.jsonl`.

### Validation

| Check | Command | Result |
|---|---|---|
| Baseline before edits | `python -m tests.run` | 25 passed, 0 failed |
| New tests | `python -m tests.run test_timing` | 7 passed: log writer (JSON lines, reserved keys protected, no-raise on unopenable path, null log); line ids and `taken`; a mock text-mode live run (record order per clip, `first`, margins, Director pairs, bootstrap flag, re-ask flag from the mock's rejected proposal, line received → taken → landed at the right segment, no dropped line); a replay video run (fire/render_done/enqueued/playing order, `due_in` null during the lead, `render_s` ≥ replay delay, probed durations, every record keeps a Unix `wall`); `/beacon` filtering (204/400/413, unknown fields and booleans-as-numbers dropped); beacons and lines landing in a live run's log; ltxv header typing and the derived `last_latency_s` |
| Full suite, final code | `python -m tests.run` | **32 passed, 0 failed** (`logs/batch-001/tests-final.out`) |
| Static check | `pyflakes` (scratch install; the repo has no linter configured) on every changed Python file | clean except one pre-existing finding: `src/eval/runner.py` `_plan`'s unused `nonlocal state` (present at HEAD) |
| **Row validation: a headless run writes the file** | `python main.py play --config configs/director.yaml --player web --port 8792 --segments 8 --log-dir logs/batch-001/real-live` + `python scripts/steer.py --player http://127.0.0.1:8792 --poll 0.5 --at '2:…' --at '5:…'` (real Director, gpt-4.1, default tier) | **Pass.** `logs/batch-001/real-live/sessions/director_live_20260922_190413/timing.jsonl`, 50 records: 8 × emit/enqueued/playing/hold_end, 5 Director pairs, 2 lines received → taken → landed (on air 5.16 s and 4.53 s after they were typed) |
| Terminal loop writes the file | `(printf …; sleep 4) \| timeout -s INT 3 python main.py play --config logs/batch-001/tools/text.mock.yaml --log-dir logs/batch-001/mock-play` | Pass. 21 records ending in `session_end` (exit 124 is the planned SIGINT) |
| Real browser: page still plays; beacons arrive | `python main.py play --config logs/batch-001/tools/replay.mock.yaml --player web --port 8793 --segments 6 --log-dir logs/batch-001/browser-replay` + headless Chromium (`logs/batch-001/tools/browse_beacons.py`, Playwright venv under `logs/_scratch/`) | Pass. All 6 clips played (video width 768, no element errors), no console or page errors, 7 beacons in the log (`waiting` on clip 1's load, 6 × `swap`: 85.8 ms for the first clip before the Watch click, 1.1-1.6 ms after). An earlier run of the same page code also produced `loadstart_on_playing` for an unprefetched first clip. Screenshot: `logs/batch-001/browser-replay.png` |

Benchmarks: not run. RUN-05 adds measurement only; the benchmark reader, baseline and gates are RUN-13 and RUN-04. No GPU was used ($0.00).

### Observed in the validation run (not caused by this batch)

The real headless run shows one stall: clip 3, the answer to the first line, was queued 0.40 s after clip 2's hold ended (`enqueued.margin` −0.397, `playing.waited` 0.397). The answering Director call took 5.15 s (`director_s`) against about 4.8 s of budget (the line arrived 0.24 s into a 5 s text clip). This is the Director intrusion that RUN-06 / X-09 address; the log now makes it visible. The Director took 2.5-5.2 s per call on the default tier; the live config uses the priority tier.

### Deviations from the plan

- The Director records are written by the engine rather than the runner, because the re-ask, fallback and Director-only time are known only there, and so the terminal loop gets them too. The runner still measures its own Director wall for the JIT estimate, unchanged.
- The spec's "reload-on-playing" beacon is named `loadstart_on_playing`, as in review 03 §7 Q1.
- Added `line_taken` and `line_dropped` so a line's path is traceable when a beat carrying it is cut before its first clip (the ARCH-06 case); ARCH-06 itself is not fixed here.
- Mid-batch fix: `render_done`'s duration was first written as `wall`, which overwrote the record's Unix time. It is now `render_s` (and the Director's whole-plan time is `plan_s`); `TimingLog.write` now refuses to let fields overwrite `t`/`wall`/`event`, with a test. All validation runs were repeated on the final code.
- `LtxvI2V.last_latency_s` became a property over `last_render` so the latency has one source.

### Known risks

- `into`/`on_air` are the loop's view of the air clock, not the browser's; the browser's lag is in the `swap` beacons.
- On the GPU box `vast_run.sh run` ships an exported tree without `.git`, so `revision` and `dirty` are null there. RUN-13's baseline identity should pass the shipped hash (review 03 §9.1).
- The serial render path (TTS spans) is instrumented but has never run; AUD-04 removes it.

### Rollback

Revert the diff of the files listed above and delete `src/util/timing_log.py` and `tests/test_timing.py`. No data migration.

### Unresolved blockers

None for RUN-05.

### Recommended next batch

RUN-13's offline pieces, starting with what reads this log: `scripts/bench_report.py` over `timing.jsonl` with the RUN-04 gates, and the offline JIT simulation test (replay backend, render delay drawn from a baseline, adversarial line timing, zero stalls asserted). If RUN-13 is too large for one batch, split it into (a) bench_report + baseline file schema and (b) the JIT simulation test, then (c) seam check, audio check and compiler replay harness. The mechanical phase-0 cleanup (CLEAN-01/02/03/07/09, LTX-08) is the other ready candidate.

### Suggested commit message

```
Every run writes a timing log, and the page reports its swaps

logs/sessions/<id>/timing.jsonl records what the live loop lives by:
per clip emit, fire (with the time left before air), render_done (the
render wall and the server's X-* headers), enqueued (margin before
air), playing (the consumer's wait: a stall) and hold_end; per viewer
line received, taken, landed or dropped; per Director call start and
end with clips, Director-only time, re-ask, fallback and cancellation;
and the page's swap, loadstart_on_playing and waiting beacons through
a new POST /beacon. Nothing reads it during a run (RUN-05).
```

Commit: not created (not explicitly permitted by the loop prompt).
