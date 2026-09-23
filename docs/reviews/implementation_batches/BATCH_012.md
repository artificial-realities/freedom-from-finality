# BATCH_012 — Narration and voices belong to the story; one render path (AUD-06, LTX-05, AUD-04)

**Status:** COMPLETED · **Date:** 2026-09-23 · **Base:** `f82edcf`

## Plan

**Why this batch.** Three approved rows on one contract, how a film speaks: the narrator as a blueprint property (AUD-06), character voices (LTX-05), and the audio path those lines take (AUD-04, whose gate is defined in terms of AUD-06's narrator). Phase 1 (AUD-06, LTX-05) plus AUD-04 (unplaced, no dependency; its validation needs the JIT simulation, done in BATCH_003). ARCH-19 needs LTX-05's voice fields and is the next batch.

**Decision IDs.**
- **AUD-06** — `Story.narrator`: enabled, tone (register), voice (description, language, accent), story-specific guidance, and an optional floor of silence. No universal frequency. `narration` (ltx / tts / off) stays a rendering choice; the hard-coded "English" and the global `voiceover_style` go.
- **LTX-05** — `Character.voice` (and `Entity.voice`): description, language, accent, delivery. The compiler uses the official single-speaker form `Name says in a {delivery} voice, "line"`. Multi-speaker composition stays gated by AUD-07.
- **AUD-04** — Delete `pending_span`, `concat_videos_and_mux_audio`, the serial branch, `_render_turn(mux_inline=False)` and `audio_seconds_owed`; every clip goes through `_bg_render_and_enqueue`. Gate = narrator enabled and silence ≥ the story's floor; the history scan goes. An overlong TTS line is rejected (the row: "rejected or re-requested, not spanned").

**Files.** `src/models/story.py`, `src/state/story_state.py`, `src/story/world.py`, `src/story/pacing.py`, `src/story/context.py`, `src/story/compiler.py`, `src/agents/director.py`, `src/prompts/director.system.md`, `src/graph/director_engine.py`, `src/models/config.py`, `configs/*.yaml`, `src/eval/runner.py`, `src/playback/mux.py`, `src/playback/__init__.py`, `src/util/media.py`, `scripts/replay_compiler.py`, `stories/lego-void/story.json`, tests, docs.

**Migrations.** A blueprint without `narrator` gets one from its `tone_guidelines` (enabled when non-empty, floor 0), so an old story keeps a narrator with the same register. `min_pause_seconds` and `voiceover_style` leave the run config (an old config that sets them still loads; the keys are ignored); the Lego story carries the same values (floor 15 s, "warm, unhurried documentary narrator", English), so its compiled prompts do not change.

**Risks / rollback.** `narration: "off"` no longer closes the gate (the Director still writes lines, they are not rendered): this follows CLAUDE.md's rule that rendering flags never gate content; no shipped config uses "off". A story that defines a narrator without a floor lets the Director speak on any beat (the owner's "no universal frequency"). Rollback: revert the commit.

**Validation required.** AUD-06: `Story.from_json`, gate and Director-context tests. LTX-05: schema/compiler tests. AUD-04: gate truth table; JIT simulation. Suite; session A replayed through the compiler.

**Baseline.** 80 passed at `f82edcf`.

## Result

### Files changed

| File | Change |
|---|---|
| `src/models/story.py` | `Voice` (description, language, accent, delivery; a plain string is a description), `Narrator` (enabled, tone, voice, guidance, `min_pause_seconds` = 0), `Character.voice`, `Story.narrator` (a model validator builds it from `tone_guidelines` when absent), `tone_guidelines` optional. |
| `stories/lego-void/story.json` | `narrator` with the former `tone_guidelines` as its tone, voice "warm, unhurried documentary narrator", English, floor 15 s; `tone_guidelines` removed; world constraint 4 no longer says "no voiceover" ("the only voice in the film is the narrator's"), resolving the contradiction the record names (Q1). |
| `src/state/story_state.py` | `narrator` from the story; `silence_seconds` starts at its floor; `audio_seconds_owed` removed. |
| `src/story/world.py` | `Entity.voice`, copied from the blueprint. |
| `src/story/pacing.py` | `voiceover_gate(state)`: `narrator.enabled and silence ≥ narrator.min_pause_seconds`; no history scan, no audio owed, no rendering flag. |
| `src/story/context.py` | `format_narrator` (the film's tone when separate, the register, "write the line in {language}", the story's guidance, or "this film has no narrator"); the cast section shows a speaking character's voice; `format_gates(narrator=…)` replaces `narration_mode`. |
| `src/prompts/director.system.md`, `src/agents/director.py` | "# The narrator" section from the story; the Director decides narrator, dialogue or silence beat by beat; dialogue `line` in the speaker's language, `delivery` as a few words. |
| `src/story/compiler.py` | `speech()`: `Name says in a {delivery} voice, "line"` (the line's delivery, then the speaker's default, then its vocal characteristics), then `in {language} with {accent}`; `narration()`: the narrator described from the story (language, voice, accent; nothing assumed). `compile_clip(narrator=…)` replaces `voiceover_style`. |
| `src/graph/director_engine.py` | Gate and compiler read the story's narrator; a TTS line longer than its clip is not rendered (a transcript note). |
| `src/models/config.py`, `configs/*.yaml` | `min_pause_seconds` and `voiceover_style` removed; `narration` documented as the rendering choice. |
| `src/eval/runner.py` | One render path: every clip is fired into `_bg_render_and_enqueue`, which mixes the TTS line when there is one; `pending_span`, the serial branch, the span bookkeeping, `_pipeline_ok` and `audio_seconds_owed` gone; `_render_turn` (the terminal loop's) always muxes. |
| `src/playback/mux.py`, `src/playback/__init__.py` | `concat_videos_and_mux_audio` removed. |
| `scripts/replay_compiler.py` | Passes the story's narrator. |
| `tests/test_narration.py` | New, 6 tests. `tests/test_story.py` updated (narrator in the compiler test; the gate moved to the new file). |
| `docs/ARCHITECTURE.md`, `CLAUDE.md`, `src/util/media.py` | The narrator, voices, one render path. |

### Validation

| Check | Command | Result |
|---|---|---|
| AUD-06 | `test_the_narrator_is_the_storys`, `test_the_director_reads_the_narrator_from_the_story` | pass: the Lego narrator; an old blueprint's tone becomes the register; no tone = no narrator; nothing narrator-related in `Config` or the YAML files; the prompt section from the story in any language |
| AUD-04 | `test_the_gate_truth_table`, `test_spanning_and_the_serial_path_are_gone`, `test_a_tts_line_takes_the_one_render_path` | pass: six gate cases (enabled × floor × silence), silence starts at the floor, no rendering flag; a fitting TTS line is muxed in the background task, a 3 s line on a 1 s clip is rejected and noted, every clip fired and queued once in order; the JIT simulation (`tests/test_jit.py`) passes on the one path |
| LTX-05 | `test_voices_are_the_storys` | pass: string and object voices reach the world and the cast sheet; the single-speaker form with delivery, voice, language and accent; the narrator in Norwegian; no language assumed |
| Session A unchanged | `replay_compiler.py run logs/batch-005/session-a.replay.json --out logs/batch-012/session-a.prompts.json --diff logs/batch-007/session-a.prompts.json` | **64 of 64 identical** (the Lego narrator phrase is unchanged; the Lego film has no dialogue) |
| Full suite | `python -m tests.run` | **86 passed, 0 failed** (80 before) |
| pyflakes | `src`, the harness, the new test | clean apart from the pre-existing `runner.py` `nonlocal state` |

### Deviations

- **CLEAN-06's narrator half landed here.** CLEAN-06 (depends on RUN-01 and AUD-06) says to remove the global narrator pause assumption under AUD-06; AUD-04's gate is defined on the story's floor, so the config's `min_pause_seconds` could not stay a second source of truth. It is gone; CLEAN-06's other half (`Shot.duration_seconds`) waits for RUN-01. The ledger marks CLEAN-06 `IN_PROGRESS`.
- The TTS path keeps the synthesis in the engine (as before) and only moves the mix into the background render task; the concurrent render-and-TTS rebuild is AUD-05's (experiment-gated) redesign.

### Next batch

ARCH-19: the story-configurable protagonist, cast and perspective policy (depends on LTX-05, now done).

### Commit message

```
The narrator and the voices belong to the story; every clip takes one render path

AUD-06: Story.narrator carries whether a film has a narrator, the
register of its lines, how it sounds (description, language, accent),
the story's guidance and an optional floor of silence; the run config's
voiceover_style, min_pause_seconds and the hard-coded "English" go, and
narration only chooses how lines are rendered. The Lego blueprint keeps
its values, so its prompts are unchanged, and no longer forbids the
voice-over its narrator speaks. LTX-05: characters may carry a voice,
and dialogue is compiled as `Name says in a ... voice, "line"`. AUD-04:
audio spanning, the serial render path and audio_seconds_owed are gone;
a TTS line is mixed in the clip's background render and rejected when
longer than the clip; the gate is the narrator and its floor.
```
