# BATCH_008 — Documentation corrections (CLEAN-04, ARCH-04, ARCH-14, ARCH-18, CLEAN-10)

**Status:** COMPLETED · **Date:** 2026-09-22 · **Base:** `2367782` plus the owner's uncommitted FINAL decision record

## Plan (written before any code changed)

### Why this batch

Phase 0's open rows after BATCH_007 are the documentation rows and LTX-09. The five documentation rows share one boundary (docs and comments; no behaviour; validation "none" in the record, so the check is that every corrected sentence is true of the code at `2367782`), so they are one batch: CLEAN-04's list of corrections, ARCH-04 (document the synchronous bootstrap), ARCH-14 (document the arc as orientation), ARCH-18 (document that the context is not cut for latency and that the opening pin is the first compaction window) and CLEAN-10 (the note on Langfuse usage). ARCH-01 is also documentation-plus-prompts, but it changes both system prompts, which is a prompt-contract change; it stays in phase 1 with the engine rows. LTX-09 (derived seeds) is a render-request change and gets its own batch.

### Decision IDs and what each becomes

- **CLEAN-04** — Every item of the row, each checked against the code first:
  - CLAUDE.md: the beat engine *has* run live (session A, 2026-09-18, commit `265e9fd`, 64 clips); viewer momentum, the 1.5× thresholds and per-story folders (`1de34a0`) have not. The Showrunner's update lands at the first *plan* that finds the task finished, not the first segment (`_land_if_done` is called from `_plan`; clips inside a beat are emitted without a plan). `lead_clips` only buffers at startup (it sets the startup gate and the queue size; the just-in-time schedule keeps its own steady-state buffer afterwards), so a line lands at N+2 or N+3 depending on when it arrives, with either setting. The compiler carries no duration: the engine's `clip_seconds` property (`clip_seconds or i2v_duration`) does. The 0.76× figure is already gone from CLAUDE.md (BATCH_002 replaced the ×RT figures with the baseline file); nothing to do there.
  - ARCHITECTURE.md: the stagnation ladder's second rung and the Showrunner's stagnation replan fire at 1.5× `stagnation_seconds`, not "twice" (`pacing_note`: `quiet < 1.5 * t`; `_trigger`: `quiet >= 1.5 * cfg.stagnation_seconds`). The claim that the compiler's order is "the order LTX-2.5 responds to" goes (nothing measured it). `stories/` is already named (BATCH_006).
  - README: the Ingredients "~1.3" claim is already gone (the README's baseline section is generated from `benchmarks/runtime/baseline.json` since BATCH_002); nothing to do.
  - `config.py`: the `clip_seconds: 0` comment says "the sum of the Director's shot durations (3-15 s)"; there are no shot durations since the beat engine, and 0 means `i2v_duration`. Fixed. The `lead_clips` comment in `config.py` and in `configs/director.live.yaml` makes the same startup-only claim wrong ("renders ahead of the player by this many clips", "a viewer's line lands about three clips after it is typed (two with 0)"); corrected under the row's "docs, comments" scope, without renaming anything (RUN-03 renames the key later).
  - Server docstring, plus the same claim in `server/README.md` and CLAUDE.md (LTX-07 says its documentation corrections are covered here, D12): the Diffusers pack ships no prompt enhancer (it loads as `None`; the server's `register_modules(prompt_enhancer=None, …)` only makes sure of it), and none is used by decision; the memory figures attributed to "dropping" it go.
- **ARCH-04** — ARCHITECTURE.md's bootstrap step says the synchronous bootstrap is the only synchronous Showrunner run and the only route by which the blueprint's `short_term_narrative` reaches the film (the Showrunner's system prompt carries it as the opening direction; no Director template has a placeholder for it), and why it stays synchronous (a few seconds during the lead build, before anything is on air).
- **ARCH-14** — One sentence in ARCHITECTURE.md: the arc orients both roles (the Showrunner keeps it current with its act marker; the Director reads it with the scene card); the code enforces nothing from it (`current_act` is clamped and displayed, nothing else reads it).
- **ARCH-18** — ARCHITECTURE.md's Director-call step says the system prompt (about 2.8K tokens, cached) is not cut for latency, since input size is not the latency term; that the opening scene summary is pinned as the first compaction window; and that the behavioural reductions (fewer recent clips, candidates hidden while a direction is active, the momentum block) wait for X-07, X-08 and X-17.
- **CLEAN-10** — The 01 review's header says Langfuse stores no usage for structured outputs; 03 found all 35 Director generations carry usage. The row says to correct the header "if the reviews are edited". The review files are the owner's untracked documents and this loop does not edit them; the correction is recorded in this report and the ledger row for whoever edits or commits the reviews.

### Files likely affected

- `CLAUDE.md`, `docs/ARCHITECTURE.md`, `server/README.md`, `server/ltx2_server.py` (docstring and one comment), `src/models/config.py` (two comments), `configs/director.live.yaml` (one comment).
- No behaviour: no Python statement, prompt, schema, config value or story changes.

### Migrations and compatibility

None.

### Risks and rollback

- None in behaviour. The one risk is a corrected sentence that is itself wrong; each is tied to the code line it describes in the Result.
- Rollback: `git checkout` of the listed files.

### Validation required

- Each corrected claim checked against the code (listed in the Result with the line that makes it true).
- `python -m tests.run` green (the config and the server module are touched in comments only; a comment edit cannot change behaviour, but the suite is the batch's boundary check); `py_compile` on the server.
- `configs/director.live.yaml` loads.

### Baseline captured

- `python -m tests.run` at `2367782`: 64 passed (`logs/batch-007/tests-final.out`, the same tree).
- The sentences to correct, as they stand, are quoted in the Result table.

---

## Result

### Purpose and decision IDs

CLEAN-04, ARCH-04, ARCH-14, ARCH-18 and CLEAN-10: the documentation rows of phase 0. No behaviour changed; every Python and YAML edit is a comment or a docstring.

### Files changed

| File | Change |
|---|---|
| `CLAUDE.md` | The beat engine's constraint names session A as its live run and lists what has not run live (momentum, the 1.5× thresholds, per-story folders); the Showrunner's update lands at the first *plan*; a line lands at N+2 or N+3 by arrival time with either `lead_clips`, which buffers only at startup; the compiler carries no duration; the offload sentence no longer says the enhancer is "dropped", and a sentence says the pack ships none and none is used by decision. |
| `docs/ARCHITECTURE.md` | Bootstrap step: the only synchronous Showrunner run and the only route for `short_term_narrative`; why it stays synchronous (ARCH-04); the arc orients both roles and the code enforces nothing from it (ARCH-14). Gates and Trigger steps: 1.5×, not "twice". Director-call step: the 2.8K-token system prompt is not cut for latency, the opening pin is the first compaction window, behavioural reductions wait for X-07/X-08/X-17 (ARCH-18); the Summaries tier row names the pin. Compile step: "in the order LTX-2.5 responds to" dropped. JIT paragraph: what `lead_clips` sets. |
| `server/ltx2_server.py` | Module docstring: the enhancer is not part of the pack and is intentionally unused; the `LTX2_OFFLOAD=gpu` line no longer says "prompt enhancer dropped". The comment above `register_modules(prompt_enhancer=None, …)` says the same. No statement changed. |
| `server/README.md` | The `LTX2_OFFLOAD=gpu` line: same correction. |
| `src/models/config.py` | The `lead_clips` and `clip_seconds` comments (startup-only lead; 0 = `i2v_duration`, no shot durations, the compiler carries no duration). |
| `configs/director.live.yaml` | The `lead_clips` comment: a startup lead that does not change where a line lands. Value unchanged. |

### The corrected claims and what makes each true

| Claim as it stood | Now | Where it is true in the code |
|---|---|---|
| CLAUDE.md: beats and just-in-time "not yet run live" | Run live once: session A, 2026-09-18, `265e9fd`, 64 clips; momentum, 1.5× thresholds, per-story folders (`1de34a0`) not yet | `git log`: `265e9fd` (beats) precedes `1de34a0` (momentum, folders, escalation); session A's transcript is dated 2026-09-18 |
| CLAUDE.md: the Showrunner's update lands "at the start of the first segment" | at the first plan (the next Director call) | `DirectorEngine._plan` calls `_land_if_done(seg)`; `emit` does not |
| CLAUDE.md: `lead_clips: 1` "makes it about N+3" | N+2 or N+3 by arrival time with either setting; `lead_clips` buffers only at startup | `run_live`: `queue_size = max(1, config.lead_clips + 1)` sizes the queue and gates the start; the JIT fire and the late Director start read the on-air clip, the queued holds and the render in flight, not `lead_clips` (record D4, verified in `_listen` and `_needed_at`) |
| CLAUDE.md: "the compiler returns 6 whatever the shots add up to" | the engine renders 6 s; the compiler carries no duration | `compile_clip` returns a string; `DirectorEngine.clip_seconds` is `int(config.clip_seconds or config.i2v_duration)`; the runner's hold reads the same |
| CLAUDE.md, server docstring, server README: "prompt enhancer dropped" (10 GB) | the pack ships none (it loads as `None`); none is used by decision | `load_pipeline`: `register_modules(prompt_enhancer=None, duration_head=None)` only when something is registered; 02 §2.6, record D12, LTX-07 rejected |
| ARCHITECTURE.md: required shift and Showrunner replan at "twice" the threshold | 1.5× | `pacing_note`: `quiet < 1.5 * t` is the middle rung, above it "REQUIRED"; `_trigger`: `quiet >= 1.5 * cfg.stagnation_seconds` |
| ARCHITECTURE.md: "in the order LTX-2.5 responds to" | dropped | nothing measured an order preference; the order is the compiler's |
| ARCH-04: the synchronous bootstrap | the only synchronous run and the only route for `short_term_narrative` | `_run_showrunner_now` is the one awaited run; `short_term_narrative` is read by `src/agents/showrunner.py` into `showrunner.system.md` ("Opening direction from the blueprint"); no Director prompt has the placeholder |
| ARCH-14: the arc | orients both roles; nothing enforced | `validator.apply_showrunner` stores `arc` and clamps `current_act`; `context.format_plan` and the transcript display it; nothing else reads it |
| ARCH-18: context size and the pin | the 2.8K cached system prompt is not cut; the opening summary is pinned as the first compaction window | `context.format_summaries` pins `scenes[0]`; the system prompt is byte-identical across a run (notes live in the user prompt) |
| `config.py`: `clip_seconds: 0` = "the sum of the Director's shot durations (3-15 s)" | 0 = `i2v_duration` | `ClipSpec` has no duration; `clip_seconds or i2v_duration` in the engine and the runner |
| README: the Ingredients "~1.3" claim | already absent | the README's baseline section is generated from `benchmarks/runtime/baseline.json` (BATCH_002); no "1.3" remains |
| ARCHITECTURE.md: `data/story.json` | already `stories/<story>/story.json` | BATCH_006 |


### CLEAN-10

The 01 review's header (`docs/reviews/01_architecture_feature_review.md`, "Evidence base") says "usage details are not stored by the wrapper for structured outputs"; review 03 §12.2 item 10 found that all 35 Director generations of session A carry usage. The review files are the owner's untracked documents and this loop does not edit them, so the correction is recorded here: when the reviews are edited or committed, that clause should read that usage is stored (03 §12.2). No decision rides on it (record D13).

### Validation

| Check | Command | Result |
|---|---|---|
| Behaviour unchanged | `git diff -U0` on `config.py`, `ltx2_server.py`, `director.live.yaml` filtered to non-comment lines | only the server's docstring lines; no statement or value changed |
| Full suite | `python -m tests.run` (`logs/batch-008/tests-final.out`) | **64 passed, 0 failed**; 31.6 s wall |
| Server module | `python -m py_compile server/ltx2_server.py` | ok |
| Config | `Config.from_yaml("configs/director.live.yaml")` | loads; `lead_clips` 1, `clip_seconds` 6 |
| Row validations | all "none" in the record | the claim table above is the check |

### Deviations from the plan

- The `lead_clips` comments in `config.py` and `director.live.yaml` were corrected along with CLAUDE.md's sentence (the row's scope is "docs, comments"; the comments made the same wrong claim). RUN-03's rename is untouched.
- Two of the row's items were already done by earlier batches (README's "~1.3", ARCHITECTURE.md's `stories/`) and are recorded as such.

### Known risks

None in behaviour. The N+2/N+3 wording rests on the record's D4 verification of `_listen` and `_needed_at`, re-read here at the queue-size and gate lines.

### Rollback

`git checkout HEAD -- CLAUDE.md docs/ARCHITECTURE.md server/ltx2_server.py server/README.md src/models/config.py configs/director.live.yaml`.

### Unresolved blockers

None.

### Recommended next batch

LTX-09 (the seed derived per session and segment, `src/i2v/ltxv.py` and the runner; locally the derivation is testable, its GPU validation is X-05's rerun), which closes phase 0. Then phase 1: ARCH-06 first (the JIT simulation can test it), then ARCH-02/ARCH-03/ARCH-05 as one engine batch.

### Suggested commit message

```
Documentation corrections: what has run live, the startup lead, the enhancer, 1.5x

CLAUDE.md names session A as the beat engine's live run and what has not
run live; the Showrunner's update lands at the first plan; lead_clips
buffers only at startup, so a line lands two or three clips after it is
typed by arrival time; the compiler carries no duration. ARCHITECTURE.md
documents the synchronous bootstrap as the only route for the blueprint's
short_term_narrative, the arc as orientation the code does not enforce,
the cached system prompt that is not cut for latency and the opening pin
as the first compaction window, and corrects "twice" to 1.5x. The server
docstring, its README and CLAUDE.md stop saying the prompt enhancer is
"dropped": the pack ships none and none is used. The clip_seconds and
lead_clips comments say what the values do (CLEAN-04, ARCH-04, ARCH-14,
ARCH-18; CLEAN-10 recorded in the batch report).
```

Commit: not created (not asked for).
