# BATCH_013 — The cast as a story policy: protagonist, supporting and dynamic characters, perspective (ARCH-19)

**Status:** COMPLETED (schema and validator tests); X-26 (post-implementation) not yet run · **Date:** 2026-09-23 · **Base:** `31b9ac0`

## Plan

**Why this batch.** ARCH-19 is phase 1's last blueprint row and depends on LTX-05 (voice fields), done in BATCH_012. One contract (the blueprint's cast model, its world representation, the validator's enforcement, both roles' prompts), one boundary (blueprint/world/validator/compiler tests with a synthetic story, plus the suite and the session A replay). One ID, large.

**Decision.** Replace the universal cast lock with blueprint fields: the protagonist (stable identity, appearance, personality, voice, starting state, what must not drift); the default camera perspective and permitted switching; named supporting characters with appearance, voice, role and entrance conditions (must / should / may); a policy and guidance for dynamically generated characters. The Showrunner decides when a supporting character serves the story, the Director stages it; every introduced character is registered with identity, appearance, voice, role, speaking permission and state; the validator protects the protagonist and enforces the story's cast and perspective rules.

**Files.** `src/models/story.py`, `src/story/world.py`, `src/story/schemas.py`, `src/story/validator.py`, `src/story/context.py`, `src/story/compiler.py`, `src/prompts/director.system.md`, `src/prompts/showrunner.system.md`, `src/agents/{director,showrunner}.py`, `src/state/story_state.py`, `src/graph/director_engine.py`, `src/llm/mock_backend.py`, `scripts/replay_compiler.py`, `stories/lego-void/story.json`, `tests/fixtures/lighthouse/story.json` (new), tests, docs.

**Migrations.** `cast_locked` is still read (into `cast_policy`); the Lego story moves to `protagonist`, `cast_policy` (no dynamic characters) and `perspective` (third-person only), the protagonist-only rollback form the record names. `WorldModel.cast_locked` is now a property of the policy; an old `world.json` with the field loads (ignored).

**Risks / rollback.** A larger blueprint and world model; a Director that introduces characters where the story allows it (X-26 measures the retry rate and identity persistence). Rollback: configure a story as protagonist-only, or revert the commit.

**Validation required.** Schema/validator tests; X-26 (post-implementation: LLM, and GPU for identity) later. Suite; session A's prompts unchanged.

**Baseline.** 86 passed at `31b9ac0`.

## Result

### Behaviour and contract

- **Blueprint.** `Story.protagonist` (name; "" = the first character), `Story.cast_policy` (`dynamic_characters`, `dynamic_guidance`), `Story.perspective` (`default`, `allowed`, `switching`). `Character` gains `role`, `personality`, `invariants`, the starting state (`posture`, `activity`, `position`) and, for supporting characters, `appears` (must/should/may) and `appears_when`.
- **World.** `WorldModel.from_story(story)`; entities carry `role`, `personality`, `invariants`, `appears`, `appears_when`, `dynamic`; the world keeps copies of the policy and perspective (the validator reads only the world). Supporting characters are registered at the start, not in the scene.
- **Schemas (strict subset).** `EntityOp` gains `role`, `speaks` (nullable bool), `voice` for introducing a character; `BeatPlan` gains `perspective` ("" = the default). These are the minimum through which the validator can enforce the row: a declared perspective to check, and the registration fields the row requires of every introduced character.
- **Validator.** A new character is refused when the story allows none (the message names the cast), when it has no `description`, or when its name doubles the protagonist's; the protagonist cannot be removed; a perspective outside `default + allowed` is refused. None of the refused ops is applied. An allowed new character is registered with its look, a short look (its first clause, ≤ 14 words, so later clips restate its identity), role, `speaks`, voice and state, flagged `dynamic`. A supporting character introduced by name is brought in (treated as an update), never duplicated.
- **Context and prompts.** `format_cast_rules` (protagonist continuity; supporting entrances; the dynamic policy and how to introduce; the perspective rule) in both system prompts. The cast sheet shows role, personality, "Never changes", and the entrance condition; characters the film introduces are listed in World now (introduced at, role, voice) and never on the cast sheet, so the cached system prompt stays byte-identical. The Showrunner is told the entrances are its call and a "must" character has to enter; the Director is told how to stage and introduce, and when to set `perspective`.
- **Compiler.** A first-person beat (declared, or the story's default) is written "First-person point of view, {framing}; …". Third-person and observer are unchanged.

### Validation

| Check | Command | Result |
|---|---|---|
| ARCH-19 tests | `python -m tests.run test_cast_policy` (the synthetic `tests/fixtures/lighthouse` story: named protagonist listed second, a "must" supporting character with a condition, dynamic characters with guidance, a first-person switch permitted "only while she looks through the telescope", two speaking voices) | 5 passed: the named protagonist in its written state; supporting cast waiting, with conditions on the sheet; Lego is protagonist-only and an old `cast_locked` blueprint maps both ways; a dynamic character registered with look, short look, role, voice, speaks and state, described on entering and restated after, in World now and not on the sheet (system prompt byte-identical); faceless, doubled and removed-protagonist ops refused and not applied; a supporting character introduced by name is brought in; a permitted switch passes and compiles first-person, a forbidden one is refused, Lego refuses first-person |
| Session A unchanged | `replay_compiler.py run … --diff logs/batch-007/session-a.prompts.json` (`logs/batch-013/`) | 64 of 64 identical |
| Full suite | `python -m tests.run` | **91 passed, 0 failed** (86 before); one earlier run of the same tree showed 90/1 with no traceback captured and three reruns passed: recorded as a possible timing flake in the live-loop tests, not reproduced |
| pyflakes | `src`, the harness, the new test | clean apart from the pre-existing `runner.py` finding |
| X-26 (post-implementation) | — | **not run**: LLM run on the lighthouse story (policy retries, introductions) and GPU for identity persistence; planned with the other LLM experiments and the GPU sessions |

### Deviations

- Two strict-subset schema fields (`EntityOp.role/speaks/voice`, `BeatPlan.perspective`) were added although the row's scope line does not list `schemas.py`: the row requires the validator to enforce perspective and every introduced character to be registered with role, voice and speaking permission, and the Director's output is the only place those can come from.
- The Lego blueprint's `invariants` are left empty: its world constraints already carry the face and body rules, and the batch adds structure, not story text.

### Next batch

The shape foundation (phase 2): RUN-01 (`ClipShape` and a validated registry; `i2v_duration` removed), RUN-08 (the warm-shape contract), CLEAN-05, CLEAN-06's duration half; then RUN-03, ARCH-10 and LTX-04 on top.

### Commit message

```
The cast is the story's policy: a protected protagonist, supporting and new characters, a perspective

ARCH-19 replaces the universal cast lock. A blueprint names its
protagonist (its starting state and what never changes), supporting
characters with an entrance condition (the Showrunner decides when, the
Director stages it), whether the film may introduce new characters and
what they should be like, and its perspective with the switches a beat
may make. The validator keeps the protagonist single and in the story,
refuses a character or perspective the story does not allow, and
registers a new character once with its look, a short look for later
clips, role, voice, speaking permission and state. The Lego story is
the protagonist-only case; a synthetic lighthouse story in the tests
exercises the rest. Session A's prompts are unchanged.
```
