# BATCH_005 — Compiler replay harness and the warm latency probe (RUN-13, part d)

**Status:** COMPLETED (RUN-13 part d; the row's offline work is complete) · **Date:** 2026-09-22 · **Base:** `5cbfe21` plus the owner's uncommitted FINAL decision record

## Plan (written before any code changed)

### Why this batch

RUN-13 (APPROVED) is the only phase-0 row in progress; parts a-c are done (BATCH_002-004). Part d is what remains of the row's recommended choice: "`scripts/latency_probe.py --warm 30` with +3% tolerance after server, i2v, shape or recipe changes" and "an offline compiler replay harness that runs a transcript's per-clip fields through both compilers". The harness is the offline prerequisite of X-05 (the LTX-01 prose compiler is compared with the current one on session A's per-clip fields) and of X-04 ("the same Director outputs replayed under each window"); the warm probe is what X-03 and every later GPU session run first. The owner asked to continue after BATCH_004 ("Go ahead").

### Decision IDs

- **RUN-13** (APPROVED), part d. If part d's offline pieces validate, the row's offline work is complete; the probe's `--warm` mode can only be validated on a GPU box, which is stated as such.

Not in scope: the LTX-01 prose compiler itself (EXPERIMENT_REQUIRED; its permitted preparation waits for RUN-01 as well), any change to `src/story/compiler.py`, the engine or the server. LTX-08 (the negative prompt) is a separate row; the probe keeps reading the config key if present.

### Where a run's per-clip fields are

`events.jsonl` (the world model's `EventRecord`) carries a clip's action, end state, on-screen list, voiceover, applied ops and its place in its beat, but not the beat's framing, the clip's camera motion, ambient sound or dialogue, and the ops only as applied text. The markdown transcript carries the compiled prompt, framing and motion. The complete input of the compiler is the Director's `BeatPlan` itself, which Langfuse stores verbatim as the Director generation's output (`metadata.turn` = the segment the plan was requested at). Checked for session A before writing this plan: 35 generations, one per Director call; every one of the 33 beats in `events.jsonl` has a generation at its first segment with the same intent; no voiceover was blanked by the gate. The compiler has not changed since that run's commit (`git diff 265e9fd HEAD -- src/story/compiler.py` is empty), so a replay through the current compiler should reproduce the transcript's prompts exactly.

The harness therefore replays the engine's deterministic emit path: a world from the blueprint, then per emitted clip (from `events.jsonl`) the beat's plan as the engine normalised it (the clip cap, the voiceover gate read from the event), `apply_clip`, `compile_clip`. Nothing under `src/` is imported for writing; the validator and compiler are called as the engine calls them.

### What is built

**`scripts/replay_compiler.py`** (new), three commands:

- `build`: writes a self-contained **replay file** from a session directory (`events.jsonl`), a Langfuse export (the format `logs/_scratch/review-03/lf_fetch.py` wrote: traces and observations) and the story folder: the beats' plans keyed by beat number and the emitted clips in order (segment, beat, clip index, user input, whether the voiceover was kept). Beats without a plan (a Director failure's fallback beat) are named in the file and skipped by the replay with a note.
- `fetch`: the Langfuse export for a session id through the public REST API, read-only, keys from `.env` or the environment (promoted from the scratch script; not covered by tests).
- `run`: replays a replay file through a compiler given as `module:function` (default `src.story.compiler:compile_clip`; the LTX-01 arm plugs in here later) with the config's `narration`, `voiceover_style` and `max_clips_per_beat`, writing per-clip prompts with word and character counts, and: `--verify transcript.md` compares every prompt with the transcript's Shot prompt (exit 1 on a mismatch); `--diff other.json` compares two compilers' outputs (word counts per clip, a unified diff); `--first`/`--count` select the chain X-05 needs (the world is always replayed from segment 1).

**`scripts/latency_probe.py`** gains `--warm N`: N renders of the baseline's shape on one warmed server, the first from the reference, the rest continuing; prompts from `--prompts prompts.json` (the harness's output, so the text encoder sees real prompt lengths) or the built-in pair; the first `cold_warm.warm_after_clips` renders are excluded as warm-up; the warm renders' client-side ratio p50, p95 and max are compared with `baseline.json`'s `renders.ratio` at `--tolerance` (+3 %), and any warm render above `gates.mid_film_compiles.factor` × the warm median is a mid-run compile; PASS/FAIL rows, exit 1 on a failure; `probe.json` records the code revision, `/health`, every row's server stage timings and the comparison. The story path moves to `--story stories/lego-void` (the reference image comes from the story's `seed_image`); the old `--seconds/--repeat` mode stays. `main` takes `argv` so a test can drive it.

**`tests/test_replay.py`** (new): (1) a mock-backend run through `run_live` with a viewer line and an introduced prop; the plans are captured by wrapping `MockBackend.generate_structured` as `tests/jit_sim.py` does; the replay file is built from them and `events.jsonl`; the replay's prompts equal the transcript's Shot prompts for every segment, `--verify` reports zero mismatches, and the chain selection and a stub second compiler's diff work; (2) `plans_from_langfuse` on a synthetic export in the fetch format; (3) `compare_warm` on synthetic rows (within tolerance, over it, a compile); (4) the probe's `--warm` mode end to end against an in-process fake LTX server (aiohttp: `/health`, `/reset`, `/generate` with the real response headers and a tiny mp4), passing at a ratio under the baseline and failing over it, with `--prompts` from (1).

Docs: `CLAUDE.md` layout lines, `docs/ARCHITECTURE.md` testing paragraph.

### Files likely affected

- `scripts/replay_compiler.py` (new), `scripts/latency_probe.py` (edited), `tests/test_replay.py` (new), `CLAUDE.md`, `docs/ARCHITECTURE.md`.
- Nothing under `src/`, `server/`, `configs/`, `stories/`, `benchmarks/`.

### Migrations and compatibility

None. `probe.json` gains fields; nothing reads it.

### Risks

- The replay depends on the validator and compiler being called exactly as the engine calls them; the round trip against the transcript is the test of that, on the mock run in the suite and on session A by hand.
- A run whose Director failed (a fallback beat) or whose Langfuse generation is missing cannot be replayed past that beat exactly; the harness says so per beat.
- The probe's `--warm` is untested on hardware in this batch; its request loop and comparison are tested against a fake server.
- `--story` changes the probe's default from `data/` to `stories/lego-void`; both exist today (CLEAN-02 deletes `data/` later).

### Rollback

Delete the two new files; `git checkout scripts/latency_probe.py`; revert the doc hunks.

### Validation required

- `python -m tests.run test_replay` passes, repeatedly; `python -m tests.run` green (baseline 53 passed).
- pyflakes on the changed files.
- Session A replayed from `logs/_scratch/review-03/lf_A.json` and verified against `logs/live-box/director-ltxv_live_20260918_145552.md`: every one of the 64 prompts equal.
- The same on the 2026-09-17 session if its Langfuse export is at hand (it is not; skipped unless fetched).
- The probe's `--warm` against the fake server in the suite; on a GPU: not run (none authorised in this batch; $0.00).

### Baseline captured

- `python -m tests.run` at batch start: **53 passed, 0 failed** (after BATCH_004's commit `5cbfe21`).
- Session A: 33 beats, 64 clips, 35 Director generations, all matched by segment and intent.

---

## Result

### Purpose and decision IDs

RUN-13, part d: the offline compiler replay harness and the warm latency probe. With this, every offline piece of RUN-13's recommended choice exists and runs in `python -m tests.run`; the probe's `--warm` mode is implemented and tested against a fake server, and awaits its first run on a GPU box (none authorised in this batch, $0.00 spent).

### Files changed

| File | Change |
|---|---|
| `scripts/replay_compiler.py` (new) | `fetch`: a session's Langfuse traces and observations through the public REST API (read-only; keys from the environment or `.env`). `build`: a replay file (`replay/1`) from a session's `events.jsonl` and the export: each beat's `BeatPlan` from the Director generation at the beat's first segment (the last generation at a segment wins, as a re-ask replaces the plan before it), the emitted clips in order with their viewer line and whether the voiceover survived the gate, and the beats without a plan listed by number. `run`: replays the file the way the engine emits (a world from the blueprint; per beat the plan capped at `max_clips_per_beat` and its voiceover blanked when the gate was closed; the viewer line registered; per clip `world.segment`, `apply_clip`, the compiler with the config's `narration` and `voiceover_style`, `opening` on segment 1), through `--compiler module:function` (default the current compiler), writing prompts with word and character counts; `--verify` against a markdown transcript's Shot prompts (exit 1 on a mismatch), `--diff` against another run's output (word counts, unified word diffs), `--first`/`--count` for a chain. |
| `scripts/latency_probe.py` | `--warm N` (the RUN-13 check): N renders of the baseline's shape on one warmed server, the first from the reference, the rest continuing; prompts from `--prompts` (a `replay_compiler.py run` output) or the built-in pair; the first `cold_warm.warm_after_clips` renders are warm-up; `compare_warm` judges the warm ratio p50, p95 and max against `baseline.json`'s at `--tolerance` (+3 %) and flags a warm render above `mid_film_compiles.factor` × the warm median; PASS/FAIL rows, exit 1 on a failure; `probe.json` records the code revision, `/health`, every row with its parsed stage timings and the comparison. The sweep mode (`--seconds`, `--repeat`) is unchanged in behaviour. `--story` (default `stories/lego-void`) replaces the hard-coded `data/` paths, the reference image is the story's seed image, and the built-in prompts no longer name a brand's ball or claw hands. `main` takes `argv`. |
| `tests/test_replay.py` (new) | Four tests. (1) A mock-backend run through `run_live` with a viewer line that introduces a prop; every `BeatPlan` is captured by wrapping `MockBackend.generate_structured`; the replay file is built from them and `events.jsonl`; the replayed prompts equal the transcript's for every segment; a deleted plan is reported as a note, not guessed; the command line's chain, verify and diff (against a stub compiler) work, and a failed verify exits 1. (2) `plans_from_langfuse` and `build_replay` on a synthetic export: the later generation wins, non-Director and unparsable outputs are skipped, a beat without a plan is listed, the voiceover flag follows the event. (3) `compare_warm` on synthetic rows: within tolerance, over it at p50, over it only at the max, a compile in the middle, too few rows; `parse_timings`. (4) The probe's warm mode end to end against an in-process aiohttp stand-in for the LTX server with the real response headers: a passing run (the reset, the first request from the reference, the baseline's 145 frames, the prompts cycling, derived seeds, the saved clips, the parsed stages), a failing run (ratio 1.0), a mid-run compile (flagged at the right index) and the sweep mode. |
| `CLAUDE.md`, `docs/ARCHITECTURE.md` | The layout names the harness and the probe's warm mode; the testing paragraph names `tests/test_replay.py`. |

### Behaviour and contract

- Nothing under `src/`, `server/`, `configs/`, `stories/` or `benchmarks/` changed. The harness calls `apply_clip` and `compile_clip` as the engine does; a change to either shows up as a `--verify` mismatch on a stored replay file, which is the point.
- A replay file is self-contained apart from the story folder and the config it is run with. The config must be the run's (`narration`, `voiceover_style`, `max_clips_per_beat`); the default is the live config.
- Runs before the beat engine (2026-09-17, commit `265e9fd`) cannot be replayed: their Director outputs are shots, not beat plans, and their events carry no beat numbers. The harness says so (`0 beats with a plan`) rather than guessing; checked on the 2026-09-17 14:34 run.
- The probe's `--warm` reads the shape, the warm-up rule and the compile factor from `baseline.json`, so a new baseline changes the check without a code change.

### Migrations and compatibility

None. `probe.json` gains fields (`mode`, `revision`, `stages`, `comparison`); nothing reads it.

### Validation

| Check | Command | Result |
|---|---|---|
| New tests | `python -m tests.run test_replay` | 4 passed; three runs 4.46-4.50 s (`logs/batch-005/test_replay-repeat.txt`) |
| Full suite | `python -m tests.run` (`logs/batch-005/tests-final.out`) | **57 passed, 0 failed** (baseline 53); 27.3 s wall |
| Static check | pyflakes (scratch venv) on the three files | clean |
| Session A replayed | `replay_compiler.py build` from `logs/_scratch/review-03/lf_A.json` and the session's `events.jsonl`; `run --verify` against `logs/live-box/director-ltxv_live_20260918_145552.md` (`logs/batch-005/session-a-verify.txt`, `session-a.replay.json`, `session-a.prompts.json`) | 33 beats with a plan, 0 without; **64 of 64 prompts equal the transcript's**; 120.0 words mean (opening 262, continuing 117.7) |
| The chain X-05 needs | `run --first 1 --count 12` (`logs/batch-005/session-a.chain12.json`) | 12 prompts, 124.2 words mean |
| `fetch` | `replay_compiler.py fetch director-ltxv_live_20260917_143416` (`logs/batch-005/lf-0917.json`) | 30 traces, 67 observations fetched; `build` on it reports 0 beats with a plan (a pre-beat-engine run, see above) |
| Probe on hardware | `latency_probe.py --warm 30` on a GPU box | **not run**: no GPU session is authorised in this batch. The request loop, the headers read, the warm-up rule, the comparison and the exit status are covered by the fake-server test. |
| RUN-13 row validation ("the offline pieces run in `python -m tests.run`") | as above | pass |

Benchmarks: none run (no GPU; $0.00).

### Deviations from the plan

- `build` keys beats by the events' beat number and warns when an event's intent differs from the plan's; session A had no such case.
- The probe's built-in prompts were made story-neutral while `--story` was added (they had named a brand's ball and claw hands); older `logs/probe/` sweeps used the old text, so a sweep-mode comparison across that boundary is not like for like. The warm mode should be run with `--prompts` anyway.
- The second local session (2026-09-17) could not serve as a second round trip: it predates the beat engine.

### Known risks

- The replay is exact only for runs on a compiler and validator equal to the current ones; that is what `--verify` measures, and a mismatch after a deliberate compiler change is the expected reading.
- Langfuse retention: the harness's exact input for a past run exists only as long as its traces do. Recording the adopted `BeatPlan` per beat in the session directory (a few lines in the engine, outside RUN-13's scope) would make every run replayable from its own files; proposed for the owner, not done.
- `fetch` depends on Langfuse's public API shape (`/api/public/traces`, `/observations`); it is untested in the suite.
- The fake server sleeps in the event loop while the probe blocks in a thread; the ratios it produces include HTTP overhead of a few milliseconds, which the test's margins absorb.

### Rollback

Delete `scripts/replay_compiler.py` and `tests/test_replay.py`; `git checkout scripts/latency_probe.py`; revert the doc hunks.

### Unresolved blockers

None for RUN-13's offline work. Its GPU-only piece, the probe's `--warm 30` on a real server, runs at the start of the first authorised session (X-03's prerequisite list already names it).

### Recommended next batch

Phase 0's remaining rows: the mechanical cleanup batch (CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-07, CLEAN-09 and LTX-08, plus the LTX-03 + CLEAN-08 compiler fixes as their own batch since they change prompts). Phase 1 then opens with ARCH-06, which `tests/jit_sim.py` can now test.

### Suggested commit message

```
A compiler replay harness, and a warm mode for the latency probe

scripts/replay_compiler.py joins a run's Director outputs (fetched from
Langfuse, where each generation is stored verbatim) with its events.jsonl
into one replay file, and replays it through the prompt compiler the way
the engine emits: the blueprint world, then per clip the plan as the
engine normalised it, apply_clip, the compiler. A replayed run reproduces
its transcript's prompts exactly (--verify; 64 of 64 on the 2026-09-18
run), and a second compiler can be diffed against the first on the same
per-clip fields (--diff), which is what X-05 and X-04 need offline.

scripts/latency_probe.py --warm N renders one shape on a warmed server
with a run's real prompts and compares the ratio tails with the runtime
baseline at +3 %, flagging a mid-run compile; tests/test_replay.py pins
both, the probe against a fake server with the real response headers
(RUN-13, part d).
```

Commit: `59f5243` on main, created on the owner's instruction after BATCH_006 closed (the shared CLAUDE.md, ARCHITECTURE.md and ledger hunks were split by batch).
