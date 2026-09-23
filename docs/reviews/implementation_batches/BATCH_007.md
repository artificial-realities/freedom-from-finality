# BATCH_007 — Compiler fixes: entering entities described in the action, camera-line grammar (LTX-03, CLEAN-08)

**Status:** COMPLETED · **Date:** 2026-09-22 · **Base:** `6d0c6bf` plus the owner's uncommitted FINAL decision record

## Plan (written before any code changed)

### Why this batch

Phase 0's remaining rows after BATCH_006: LTX-03 + CLEAN-08 (the two approved bug fixes in the active compiler; LTX-03 depends on CLEAN-08 "if the old compiler remains active", which it does while LTX-01 is experiment-gated), the documentation rows (CLEAN-04, ARCH-04, ARCH-14, ARCH-18, CLEAN-10) and LTX-09. The two compiler rows share one file, one validation boundary (compiler tests plus the replay harness on session A) and one risk (every prompt changes), so they are one batch. Both are `APPROVED`; §5 lists CLEAN-08 as safe cleanup and excludes LTX-03 because it changes prompt semantics, which is why the batch reports the exact prompt diff on the one live run.

Not in scope: the LTX-01 prose compiler (EXPERIMENT_REQUIRED; it inherits both rules later), the Director prompt's description of `camera_motion` as a verb phrase (LTX-04, phase 2+, depends on RUN-01), any change to the schemas or the Director prompt.

### Decision IDs and what each becomes

- **CLEAN-08** — Today the compiler writes `{framing}, {camera_motion} camera.`, which is grammatical only for "static": session A has "slow push-in toward the pair camera" and three more like it (02 §4.1). The compiler writes `{framing}; the camera remains static.` for an empty, "static" or "none" motion, and otherwise a clause built from the motion text as the Director writes it today (a noun phrase such as "slow push-in toward the pair") or as LTX-04 will later ask for (a verb phrase such as "slowly pushes in"): a motion whose head word is a third-person verb gives `the camera {motion}`, an `-ing` head gives `the camera is {motion}`, anything else gives `the camera makes a {motion}`; a leading "the camera"/"camera" in the motion is not doubled. Session A's ten distinct lines all come out grammatical.
- **LTX-03** — Today every entity in `clip.on_screen` is listed under "On screen:" as present from the first frame, with its description only when `first_seen == segment_no`; an entity introduced by an op but not listed in `on_screen` (session A segment 53, the crown) is never described anywhere, and a returning entity gets its name only. The compiler now separates **entering** entities from the on-screen list: an entity introduced this segment (whether or not the Director listed it, if the world has it present), and an on-screen entity that was not in the previous clip's picture. The previous clip's picture is the last event record's `on_screen` plus whatever entered during that clip (entities with `first_seen` equal to its segment), so an entity described as entering once is not described as entering again on the next clip (session A segment 54). Entering entities are never listed under "On screen"; they are described in full, before the action, as `Entering the picture during this clip: {name} ({look}); …`, with the look omitted when the Director's action already contains it verbatim. The on-screen list keeps today's form for the entities that were already in the picture. `DESCRIBED_IN_FULL` bounds the described entities across both (entering first).
- The engine appends the clip's event record after compiling, so the compiler reads the previous clip's picture from the world model (`world.events[-1]`), the system of record; the replay harness appends the same record so a replayed run matches the engine (the mock-run round trip in `tests/test_replay.py` is the check).

### Files likely affected

- `src/story/compiler.py` (both rows), `scripts/replay_compiler.py` (the event record per replayed clip), `tests/test_story.py` (compiler tests), `tests/test_replay.py` (if the round trip needs a returning entity).
- `docs/ARCHITECTURE.md` (the compile step), `CLAUDE.md` (one clause in the constraints paragraph).
- Nothing under `src/prompts/`, `src/story/schemas.py`, `src/story/validator.py`, the engine, configs or stories.

### Migrations and compatibility

- Every compiled prompt changes (the camera clause on all clips; the entering sentence on introductions and re-entries). A stored replay file replayed through the new compiler no longer matches its old transcript, by design; the `--diff` against the old compiler's output is the record of the change.
- `EventRecord` and the replay file format are unchanged.

### Risks and rollback

- Medium by category (prompt semantics on the GPU path) and unmeasured on hardware: no GPU is authorised in this batch. The entering rule depends on the Director's `on_screen` lists being consistent from clip to clip; an omission would make an entity "enter" a clip it was already in (session A has one such omission, at 53, where the rule corrects it, and none where it would mislead). Rollback: `git checkout` of the compiler and the harness.
- Session A's re-verification will report 64 mismatches; that is the expected reading, and the diff is saved.

### Validation required

- `python -m tests.run` green (baseline 62 passed); compiler tests for: static and moving camera lines in noun-phrase, verb-phrase and `-ing` forms; a prop introduced off-screen (not in `on_screen`) gets its look on first appearance; a returning dormant prop gets its identifiers again; an entity described as entering is listed normally on the next clip; an entity whose description the action already contains is named without it; the opening clip keeps the reference-pose sentence and the on-screen list; the cap.
- The replay harness: `replay_compiler.py run` on session A's replay file with the new compiler, `--diff` against `logs/batch-005/session-a.prompts.json` (the old compiler's output); the diff must touch exactly the camera line on every clip and the entering sentence on segments 35, 49 and 53, and nothing else.
- The mock-run round trip in `tests/test_replay.py` (engine and harness agree on the new compiler).
- pyflakes on the changed files.

### Baseline captured

- `python -m tests.run` on `6d0c6bf`: **62 passed, 0 failed** (`logs/batch-007/tests-baseline.out`).
- Session A (`logs/batch-005/session-a.replay.json`, 64 clips): camera lines: 53 "static", 9 push-ins/pans as noun phrases, 1 "low angle" framing; introductions at 35 (monster), 49 (princess), 53 (crown, not in `on_screen`); at 54 the crown appears in `on_screen` for the first time. The old compiler's prompts: `logs/batch-005/session-a.prompts.json`.

---

## Result

### Purpose and decision IDs

LTX-03 (entering and returning entities described in the action's clip, never listed as already present) and CLEAN-08 (camera-line grammar), both in the active compiler. The LTX-01 experiment arm, when it is built, inherits both rules (the record says so for CLEAN-08; LTX-03's rule is the entering/returning condition the arm's "entering entities inside the action" needs).

### Files changed

| File | Change |
|---|---|
| `src/story/compiler.py` | `camera_clause(motion)`: "the camera remains static" for an empty, "static" or "none" motion; otherwise the Director's motion after "the camera" as a verb phrase (`the camera slowly pushes in`), a participle (`the camera is tracking him`) or a noun phrase (`the camera makes a slow push-in toward the pair`), classified by the head word; a leading "the camera" is not doubled. The framing line is `{framing}; {clause}.` `previous_picture(world, segment_no)`: the previous clip's `on_screen` plus what entered during it, from the world's event log; None when the previous clip is not on record. `compile_clip` splits the on-screen entities into **entering** (introduced this segment, listed or not, if present; or on screen now and not in the previous picture) and **staying** (the "On screen:" list as before), writes `Entering the picture during this clip: …` with full looks before the action (the look left out when the action carries it verbatim), and spends `DESCRIBED_IN_FULL` on characters first, then entering, then staying. Docstring updated. |
| `scripts/replay_compiler.py` | After compiling each clip the harness appends the clip's `EventRecord` as the engine does, so the compiler reads the same previous picture in a replay. |
| `tests/test_story.py` | Existing compiler tests updated to the new camera line and the entering form. New: `test_compiler_camera_clause` (static forms, session A's noun phrases, verb phrases, a participle, no doubled "the camera", the framing line) and `test_compiler_describes_entering_and_returning_entities` (an introduction is an entrance, not a presence; the next clip lists it by name; a prop introduced by an op the Director left out of `on_screen` is described; the clip after that lists it without a second entrance; a dormant prop returning gets its identifiers; a character back after a clip out of frame gets the full look, characters first; a look the action already carries is not repeated; an entity introduced off screen does not enter; no previous record means nothing is called a return). |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | The compile step and the constraints paragraph describe the entering sentence and the camera clause. |

### Behaviour and contract

- Every compiled prompt changes in the camera line. Introductions and re-entries move from "On screen" to an "Entering the picture during this clip" sentence placed before the action, with the entity's look. An entity the Director introduced by an op but did not list in `on_screen` is now described (session A segment 53, the crown).
- "The previous clip's picture" is the last event record's `on_screen` plus the entities whose `first_seen` is that record's segment. It is read from the world model, the system of record; the engine already appends the record after compiling, and the harness now does the same. When the last record is not the previous segment (a replay with a gap, or a test without records), nothing is treated as returning.
- Nothing under `src/prompts/`, `src/story/schemas.py`, `src/story/validator.py`, the engine, configs or stories changed. The Director's `on_screen` semantics are unchanged: ids visible in the clip, including what enters during it.

### Migrations and compatibility

None in data. A stored replay file replayed through the new compiler no longer matches its old transcript, by design; the diff below is the record.

### Validation

| Check | Command | Result |
|---|---|---|
| Compiler tests | `python -m tests.run test_story` | 15 passed (13 before; the two new tests above) |
| Full suite | `python -m tests.run` (`logs/batch-007/tests-final.out`) | **64 passed, 0 failed** (baseline 62); 31.7 s wall |
| Engine and harness agree | `tests/test_replay.py::test_replay_reproduces_the_engine_prompts` (a mock run with an introduced prop, replayed and compared with the transcript) | pass on the new compiler |
| Static check | pyflakes (scratch venv) on `compiler.py`, `replay_compiler.py`, `test_story.py` | clean |
| Session A replayed through the new compiler | `replay_compiler.py run logs/batch-005/session-a.replay.json --out logs/batch-007/session-a.prompts.json --diff logs/batch-005/session-a.prompts.json` (`logs/batch-007/session-a-diff.txt`, `session-a-summary.txt`) | 64 of 64 clips compiled; **61 differ in the camera line only, 3 also elsewhere: segments 35 and 49 (the monster and the princess move from "On screen" to the entering sentence) and 53 (the crown, never described before, is described)**; segment 54 lists the crown under "On screen" with no second entrance; nothing else changed. All 11 distinct camera lines are grammatical ("…; the camera remains static", "…; the camera makes a slow push-in toward the pair", "…; the camera makes a slow pan to follow Lego Man's movement", …). Words per prompt 120.0 → 122.6 mean. |
| Session A verified against its transcript | `replay_compiler.py run … --verify logs/live-box/director-ltxv_live_20260918_145552.md` (`logs/batch-007/session-a-verify.txt`) | 0 of 64 equal, exit 1: the expected reading after a deliberate compiler change (every camera line changed) |
| Row validations | LTX-03 "prop introduced off-screen gets its look on first appearance; a returning dormant prop gets identifiers"; CLEAN-08 "compiler test" | pass (both in `test_compiler_describes_entering_and_returning_entities` and `test_compiler_camera_clause`) |
| On hardware | — | **not run**: no GPU authorised in this batch ($0.00). The effect of the new wording on the picture is unmeasured; X-05 compares compilers on this chain later, and the next live run's transcript carries the new prompts. |

### Deviations from the plan

- The record's CLEAN-08 text gives `the camera {motion}`; taken literally with today's noun-phrase motions ("slow push-in toward the pair") that is still not a sentence, so the clause form depends on the motion's head word (verb, participle or noun phrase). Session A's ten moving lines all become "the camera makes a …". LTX-04 later asks the Director for verb phrases, which take the `the camera {motion}` form directly.
- `DESCRIBED_IN_FULL` now spends its budget characters first, then entering entities, then the rest (before: on-screen order, characters first). Same count; the identity look survives a crowded clip.

### Known risks

- The entering rule trusts the Director's `on_screen` lists clip to clip. An entity omitted at clip N and listed at N+1 while it was in the picture all along would be described as entering N+1; session A's one omission (53) was an introduction, which the rule handles, and no clip would have misfired. A Director-side omission in a future run costs one redundant description, not a contradiction with the picture.
- Unusual camera heads ("handheld", "focus pull") give "the camera makes a handheld"; the verb-phrase form LTX-04 will ask for avoids the heuristic.
- The prompt change is unmeasured on the GPU (above).

### Rollback

`git checkout HEAD -- src/story/compiler.py scripts/replay_compiler.py tests/test_story.py docs/ARCHITECTURE.md CLAUDE.md`.

### Unresolved blockers

None.

### Recommended next batch

The documentation batch: CLEAN-04 (CLAUDE.md, ARCHITECTURE.md, README, the `config.py` comment, the server docstring) with the doc-only ARCH-04, ARCH-14, ARCH-18 and CLEAN-10. Then LTX-09 (derived seeds; its GPU validation waits for X-05), and phase 1 opens with ARCH-06.

### Suggested commit message

```
The compiler describes what enters the picture, and writes the camera as a clause

An entity that enters the picture during a clip (introduced in this
segment, listed by the Director or not, or on screen without having
been in the previous clip's picture) is described with its look in an
"Entering the picture during this clip" sentence before the action and
is never listed as already on screen, since the conditioning frames do
not have it; the previous picture is read from the world's event log,
and the replay harness records events as the engine does. The framing
line ends in a camera clause: "the camera remains static", or the
Director's motion after "the camera" in a form that reads as a sentence
(LTX-03, CLEAN-08). On the 2026-09-18 run this changes the camera line
of all 64 prompts and adds the entering sentence on segments 35, 49 and
53 (the crown, which the Director had not listed, is now described).
```

Commit: not created (not asked for).
