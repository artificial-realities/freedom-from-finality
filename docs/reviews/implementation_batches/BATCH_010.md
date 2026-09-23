# BATCH_010 — The Showrunner's clock and memory, and the authority rule (ARCH-01, ARCH-02, ARCH-03, ARCH-05, ARCH-16, ARCH-17)

**Status:** COMPLETED (ARCH-02, ARCH-03, ARCH-05, ARCH-17); ARCH-01 and ARCH-16 implemented, their transcript review pending · **Date:** 2026-09-23 · **Base:** `571a58a`

## Plan

**Why this batch.** Phase 1's rows on the Showrunner's side of the handshake share one contract (when a run is triggered, spawned and landed, and what it keeps) and one validation boundary (engine-level tests with a stub LLM, plus the suite). ARCH-01 (the authority rule in both system prompts) and ARCH-16 (thread age plus the Showrunner's instruction) are prompt changes on the same two roles; ARCH-17 is the landing's note filter. Six IDs; one subsystem. The viewer-side rows (ARCH-06, ARCH-07, ARCH-11) are the next batch.

**Decision IDs.**
- **ARCH-02** — A held `scene_done`/`needs_replan` is stored on the engine and raised again at every plan until a replan run spawns; a landed replan (any new card) clears it.
- **ARCH-03** — The trigger is still decided in `plan()`; the task is spawned from `emit()` of the beat's first clip after `apply_clip` and the event record, so the snapshot and the window `..at_segment` include the spawn clip. A beat cut before its first clip spawns nothing (its flag stays held under ARCH-02).
- **ARCH-05** — The Director's REQUIRED rung stays at 1.5×; the Showrunner's stagnation replan moves to 2×. ARCHITECTURE.md already said 1.5× (BATCH_008); it now names both rungs.
- **ARCH-16** — `format_threads` shows each thread's opening segment and age (segments and story seconds). The Showrunner is told to decide continue / resolve / abandon for stale threads and name the decision in `rationale`; no code expiry. A story may carry optional `thread_guidance`.
- **ARCH-17** — Notes are kept on characters only; a note that restates the cast sheet (contained in it, or ≥ 75 % of its significant words drawn from it) or is contained in a summary (stored or in the same update) is not kept; at most `MAX_NOTES` (4, the number the context shows) per character, oldest first out.
- **ARCH-01** — Both system prompts, ARCHITECTURE.md and CLAUDE.md state the rule: the Director is the only creative call on the critical path and outranks the card while a viewer direction is active; the card is the menu for the quiet stretches; the Showrunner owns memory and authors the idle film.

**Files.** `src/graph/director_engine.py`, `src/story/pacing.py`, `src/story/validator.py`, `src/story/context.py`, `src/story/world.py`, `src/agents/showrunner.py`, `src/models/story.py`, `src/state/story_state.py`, `src/prompts/director.system.md`, `src/prompts/showrunner.system.md`, `tests/test_showrunner_clock.py` (new), docs.

**Migrations.** None in data. Compaction windows move by one clip (the spawn clip is now inside its own window); old sessions keep their labels. `Story.thread_guidance` is optional.

**Risks / rollback.** ARCH-02 can cause more replans when flags cluster, bounded by `showrunner_min_gap`. ARCH-03 moves the spawn from the plan to the first clip's emit, a few hundred ms to seconds later in the live loop; nothing waits on it. The note filter could reject a legitimate note that happens to be in a summary (by design of the row). Rollback: revert the commit.

**Validation required.** ARCH-02: a held replan spawns at the first plan past the gap. ARCH-03: windows contiguous and inclusive of the spawn segment; the snapshot holds clip 1's ops. ARCH-05: threshold order. ARCH-17: filter unit test. ARCH-16 and ARCH-01: transcript review (a run with scripted lines). Suite.

**Baseline.** 69 passed at `571a58a`.

## Result

### Files changed

| File | Change |
|---|---|
| `src/graph/director_engine.py` | `_held_trigger` (reason, mode, first segment) set by a beat's `scene_done`/`needs_replan`, raised by `_trigger` as "… (held since segment N)" after the current beat's own flags and before stagnation, cleared when a replan run spawns or any new card lands. `_pending_spawn` replaces the spawn in `_plan`; `emit()` spawns it on the beat's first clip after the event record is appended; `abort_beat` before the first clip drops it with a transcript note. Stagnation replan at `SHOWRUNNER_REPLAN_AT` (2×). Module docstring. |
| `src/story/pacing.py` | `REQUIRED_AT = 1.5`, `SHOWRUNNER_REPLAN_AT = 2.0`; `pacing_note` uses the first; `stagnation_report` states all three rungs in seconds. |
| `src/agents/showrunner.py` | The compact instruction's replan judgement refers to "your replan threshold" (2×); the system prompt gets `thread_guidance`. |
| `src/story/validator.py` | `redundant_note()` and the note rules in `apply_showrunner` (characters only; restated or summarised notes not kept, with a transcript line saying why; cap with the dropped notes named). |
| `src/story/world.py` | `MAX_NOTES = 4`; `WorldModel.seconds_since(segment)`. |
| `src/story/context.py` | Thread lines carry `opened segment N, M segments, S s of film ago`; notes shown up to `MAX_NOTES` (the separate `NOTES_SHOWN` is gone). |
| `src/models/story.py`, `src/state/story_state.py` | Optional `thread_guidance`. |
| `src/prompts/director.system.md` | "Who decides what" paragraph (ARCH-01). |
| `src/prompts/showrunner.system.md` | "Who decides what" paragraph (ARCH-01); notes are about characters and never restate the blueprint or a summary; the thread-age principle with continue / resolve / abandon; `# Threads in this story`; `rationale` carries the thread decisions. |
| `tests/test_showrunner_clock.py` | New, 8 tests (below). |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | The authority rule, the trigger/spawn split, held flags, the 2× rung, note and thread rules. |

### Validation

| Check | Command | Result |
|---|---|---|
| New tests | `python -m tests.run test_showrunner_clock` | 8 passed: held replan spawns at the first plan past the gap and is cleared (ARCH-02); a new card clears a held flag (ARCH-02); the Showrunner prompt of the spawned run contains the spawn clip's `introduce prop red_hat` and not the beat's second clip, windows are contiguous and the first ends on the spawn segment (ARCH-03); a beat cut before its first clip spawns nothing and the next plan's first clip spawns the held flag (ARCH-03); 140 s → REQUIRED without a Showrunner replan, 185 s → both (ARCH-05); thread lines with age (ARCH-16); the Showrunner system prompt carries the story's guidance or the default, and the authority rule (ARCH-16, ARCH-01); notes: session A's bootstrap note (pose + look) rejected as a restatement, a prop note not kept, two notes contained in summaries not kept, a duplicate not kept twice, the cap drops the oldest (ARCH-17) |
| Mutation check | the new tests against `HEAD`'s `director_engine.py` | 5 of 8 fail on the old engine (the three that pass are the context, prompt and validator tests, which do not touch the engine) |
| Full suite | `python -m tests.run` | **77 passed, 0 failed** (69 before) |
| pyflakes | changed modules | clean (pre-existing findings only) |
| ARCH-01 / ARCH-16 transcript review | — | **not yet run**: needs a run with the real Director and scripted lines; planned as the phase-1 headless run after the viewer-path batch (and X-07/X-08 produce such transcripts) |

### Deviations

- ARCH-05's scope names `_trigger`, `pacing.py` and docs; the Showrunner's compact-mode instruction also judged stagnation (at 1×) and would have kept a second owner on the lower rung, so its wording now points at the 2× rung stated in the stagnation report.
- ARCH-16's "explicit decision" is carried in `rationale` (the row's scope is prompts and context, no schema change).
- ARCH-17's numbers (75 % word overlap, ≥ 4 significant words, cap 4) are this batch's; the cap equals what the context already showed, so no stored note is ever unseen.

### Rollback

`git revert` of the batch commit.

### Next batch

The viewer path: ARCH-06 (a second line after a plan returned is joined), ARCH-07 (`delivered_request`), ARCH-11 (answer beats capped at two clips), then a headless run with scripted lines for the phase-1 transcript reviews.

### Commit message

```
The Showrunner remembers a held replan, snapshots after the clip that caused it

ARCH-02: a scene_done or needs_replan flag that has to wait (a run in
flight, or inside showrunner_min_gap) is remembered and raised again at
every plan until a replan run takes it or a new card lands. ARCH-03: the
run is triggered at the plan and spawned after the beat's first clip is
applied, so its snapshot and its compaction window include that clip.
ARCH-05: the Director is told a shift is required at 1.5x the
stagnation threshold, the Showrunner replans at 2x. ARCH-16: threads
show their age and the Showrunner decides continue, resolve or abandon
(a story may add thread_guidance). ARCH-17: only characters keep notes,
at most four, never a restatement of the cast sheet or a summary.
ARCH-01: both system prompts state who decides what.
```
