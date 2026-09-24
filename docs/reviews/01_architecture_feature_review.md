# Architecture and feature justification review (2026-09-22)

An investigation, not a change. No product code, prompt, schema, config or test was edited. Two scratch scripts ran in the session's scratchpad (outside the repository): one reconstructs the Director and Showrunner prompts from a saved world model and counts tokens, the other reproduces a viewer-input race in the live loop.

**Sources.** `PROJECT_SYSTEM_AUDIT.md` (2026-09-18) read in full; every file the prompt lists, plus `src/eval/runner.py`, `src/state/story_state.py`, `src/models/*`, `src/i2v/ltxv.py`, `src/playback/player.py`, the three configs, both story blueprints, the test suite (25 of 25 pass on the mock backend), `docs/ARCHITECTURE.md`, and the reviews of 2026-09-08 and 2026-09-17. The code at HEAD (`1de34a0`) is treated as authoritative; the audit's line numbers match it.

**Evidence base.** The only live run of the beat engine is `logs/live-box/director-ltxv_live_20260918_145552` (called **session A** below): 64 clips of 6 s, 33 beats, gpt-4.1 on the priority tier, an H100 box. It ran on commit `265e9fd`, before the last commit added viewer momentum, the 1.5× stagnation thresholds and the per-story folders, so those three are unverified live. Its Langfuse traces give per-call latency (usage details are not stored by the wrapper for structured outputs). Prompt sizes below are reconstructed from session A's saved `world.json` at its final state and counted with the o200k tokenizer that gpt-4.1 uses.

| Session A, measured | Value |
|---|---|
| Director calls (Langfuse) | 35 calls, mean 2.98 s, median 2.91 s, min 1.73 s, max 4.37 s |
| Showrunner calls (Langfuse) | 8 calls, mean 3.70 s, max 4.82 s; 7 landed |
| Beats planned | 33: 28 of two clips, 3 of three, 2 of one; the cap of four never reached |
| Beats cut short by the viewer | 2 (segments 34 and 47), both after clip 1 |
| Viewer lines | 4, all absorbed (segments 20, 28, 35, 48) |
| Validator retries, warnings | 0 and 0 in 64 clips |
| Scenes | scene 1 = segments 1-37 (222 s), scene 2 = 38-64 (162 s, still open at the end) |
| `scene_done` set by the Director | never; both scene changes came from `needs_replan` beside a viewer line |
| Voice-over lines | 16 in 64 clips (one beat in two) |

---

## 1. Executive summary

1. **The two-role split is justified for memory and unproven for planning.** The Showrunner's compaction keeps the Director's prompt bounded (2.0K user tokens after 64 clips, against 841 tokens of raw events for ten clips) and stays off the critical path, which the 2026-09-17 investigation showed was the whole visible problem. Its planning half, the scene card, was followed almost verbatim by the Director when the viewer was silent and overridden whenever the viewer spoke. That is a workable arrangement, but it is not a hierarchy: two authors write against each other through a card that is stale by construction and a replan flag that can be dropped. Recommendation: **keep the split, clarify authority, repair the handshake.** Do not merge the roles now; the Director's output is the per-beat critical path (3 s at priority tier) and compaction would put it back near the 6 s the old engine measured.
2. **The handshake has four concrete defects**, all visible in session A or reproduced offline: the Showrunner snapshot is taken before the triggering beat's first clip is applied, so every compaction window omits one clip (the one carrying the beat's viewer disposition and thread ops) and a replan card is written without the change that caused it; a held `needs_replan` or `scene_done` is silently dropped rather than re-evaluated (segment 48: the princess story ran 16 clips under a card about "a first prop, e.g. a soccer ball"); an update lands at the next *plan*, so up to one beat after the call returns; and a second viewer line arriving between a Director return and its first render is answered while the first line is invisible to the Director (reproduced with the mock backend).
3. **Beats work as designed and the cap of four is not what governs them.** The prompt's "most beats are two or three clips" is the operative control; the Director wrote two clips in 85% of beats. The cap is a compromise, neither principled nor harmful. Interactivity is not limited by beat length (the viewer's line drops unrendered clips) but by the render queue.
4. **The "61% boilerplate" figure is about the compiled video prompt under the old compiler, not the Director prompt.** Today a continuing clip's video prompt is 118 words on average, 46% of it the fixed anchor and short look, which the 2026-09-17 probe showed is needed. The Director prompt is 2.8K stable system tokens (1.8K instructions, 1.0K blueprint) plus 0.2K to 2.0K of moving user tokens. Nothing in it is worth cutting for speed; output tokens set the latency. Several facts appear two or three times, and two data paths are dead (prop notes are written but never shown; character notes beyond four vanish from the prompt while staying in the model).
5. **Actual bugs, separated from valid design:** the dropped held trigger, the one-clip compaction gap, the double-line race, a deferred request resolved by position rather than identity, a prop introduced without being on screen (or returning from dormancy) never gets its look into a video prompt, and the resume path in `_init_world` is unreachable. Confusing but valid: the two description caps, the lenient location resolver, the synchronous bootstrap, the `i2v_duration` fallback.
6. **What needs a decision or an experiment rather than a fix:** whether the viewer's direction should survive a scene card written after it (momentum reset scope), whether momentum earns its place at all (it duplicates two other signals and has never run live), whether the planning half of the Showrunner is needed while the viewer is active, and whether voice-over on every other beat is the intended density.

---

## 2. Current authority model

### 2.1 Who decides what

| Decision | Owner | Where | Can be overridden by |
|---|---|---|---|
| What happens in the next 6-24 s: action, end state, camera, sound, dialogue, entity ops per clip | Director | `BeatPlan` (`src/story/schemas.py:81-92`), `plan()` (`src/graph/director_engine.py:113-174`) | validator drops inadmissible ops (`validator.py:115-273`); engine caps clips (`director_engine.py:150`), clears voice-over (`:151-152`), forces `scene_done` (`:154-157`) |
| How many clips the beat has (1-4) | Director, within the cap | prompt `director.system.md:48`; cap `config.max_clips_per_beat` | viewer's line cuts it (`abort_beat`, `director_engine.py:182-212`) |
| Whether the viewer's line is absorbed, deferred or declined | Director | `BeatPlan.intervention`; applied on clip 1 (`validator.py:251-269`) | validator turns `none` into `deferred` when a line was pending (`:254-256`) |
| Whether the scene is over or the plan no longer fits | Director (`scene_done`, `needs_replan`) and engine (scene-length cap) | `_trigger` (`director_engine.py:343-356`) | held and **dropped** if a run is in flight or the gap is unmet (`_hold_reason`, `:383-397`; see §5.2) |
| The arc, the scene card (goal, tension, exit, intensity, candidate beats), which threads to drop | Showrunner | `ShowrunnerUpdate` (`schemas.py:108-119`); `apply_showrunner` (`validator.py:276-333`) | nothing: a landed card replaces the previous one unconditionally; the Director may ignore it (prompt: "a request outranks the scene card") |
| What the world contains, who holds what, where everyone is | Validator, from Director ops | `apply_clip` | nothing; the world is the system of record |
| Compaction: what the story so far *was* | Showrunner | summaries, act summary, entity notes | nothing; summaries are never revised |
| When each role runs | Engine | `plan()`, `_trigger`, `_hold_reason`, `_land_if_done` | the live loop cancels a Director call when the viewer types (`runner.py:490-514`) |
| Pacing pressure, scene length, voice-over gate | Code | `src/story/pacing.py` | none; the Director reads them as text |
| The video prompt | Compiler | `compile_clip` (`src/story/compiler.py:66-127`) | none |
| Clip length | Engine (`clip_seconds` or `i2v_duration`) | `director_engine.py:92-94` | the runner re-derives it in three places (§7.4) |

### 2.2 Why both roles exist

The 2026-09-08 review's evidence for decomposition was narrow: the old four-role engine beat a single call on late-game prop persistence, and the stated mechanism was that memory compression was done by a role other than the writer. That review proposed exactly what exists now: one writer on the fast clock, one memory-and-planning role on a slow clock. The two roles communicate only through the world model. The Showrunner writes `summaries`, `act_summary`, `Entity.notes`, `plan.arc`, `plan.scene`; the Director reads them rendered by `context.py` and writes `events`, entity state, `threads`, `interventions`. Neither sees the other's reasoning; the Showrunner sees the Director's `beat_intent` through the event log (`context.py:203-205`).

The **memory half** earns its place on the numbers. After 64 clips the Director's user prompt is 2.0K tokens; the raw events for ten clips alone are 841 tokens (§6.2). Without compaction the prompt would grow by roughly 80 tokens a clip and the Director would be reading a transcript. Compaction on a slow clock, off the critical path, is the cheapest way to bound that, and session A shows it working: seven runs, none stalled a segment, summaries readable.

The **planning half** is weaker. In session A the arc was written once at bootstrap and never changed in seven runs. Scene 1's five candidate beats were executed by the Director as beats 1 to 9 nearly in order (stretch, walk, head spin, claw hands, jump, sit and lie down; transcript lines 39-660). After each viewer excursion the Director returned to the list: the breakdance line at 20 held for two beats, then beat 12 was "tests his claw hands' grip", a card candidate. The prompt says candidates are "options, not a script" (`director.system.md:34`); observed, they are the script whenever the viewer is silent. That is not a fault. A film that keeps moving without input needs a source of things to do, and a menu written on a slow clock with the whole history in view is a reasonable source. But it means the Showrunner is the de facto author of the film's quiet stretches, and the Director is an executor plus the viewer's advocate. The design documents describe the Director as "the only creative voice on the critical path"; on the evidence it is the only creative voice *when the viewer speaks*.

### 2.3 Verdict: two authorities, and the boundary between them is time

The split is clean on paper because the two roles never write the same field. It is not clean in practice because they write the same *story* at different times with different information:

- The Showrunner plans from a snapshot that predates the beat which triggered it (§5.1), and its card lands one to three segments later, after the Director has already moved on.
- The Director can override the card at will but cannot reliably tell the Showrunner that the card's premise is gone: `needs_replan` is a one-shot flag that the engine forgets when it cannot act on it (§5.2).
- A viewer direction set after a Showrunner was spawned is erased when that run lands, if it replans (§5.4).

None of these is an argument for merging. All three are handshake defects with small, deterministic fixes (§9). The open product question is whether the planning half should exist while the viewer is active, or only as the idle author (§10, Q1-Q2). That is an experiment (§11, E2), not a code judgment.

### 2.4 Bootstrap

`plan()` runs the Showrunner synchronously when `plan.scene is None` (`director_engine.py:132-135`, `_run_showrunner_now` `:373-381`). It is the only synchronous Showrunner call and costs one LLM round trip before the first Director call: 2.8 s in the 2026-09-17 session, about 3.7 s by session A's Showrunner mean. If it returns nothing, `_land` installs a generic card (`:440-449`).

Is it justified? The Director never sees the blueprint's `short_term_narrative` or `long_term_narrative`: those go only into the Showrunner's system prompt (`showrunner.py:76-78`; the Director's template has no such placeholder). So without a bootstrap the film opens on the fallback card, "Open the film on the protagonist and let the first idea arrive," and the blueprint's authored opening ("wakes up, tests a few moves, discovers the first prop within the first minute") reaches nobody. Given that, the synchronous call is the simplest correct choice and its cost is one call at startup while the lead is being built anyway (two clips of render, about 10 s). Two cheaper designs exist: derive the opening card deterministically from `short_term_narrative` and run the bootstrap asynchronously (it lands at the second or third plan), or give the Director the opening direction on segment 1 only. Both change what the first one or two beats are guided by. Classification: **keep but document**, with an optional experiment (E8) if startup time matters.

---

## 3. Beats, clips and creative granularity

### 3.1 What a beat is

Operationally a beat is one Director call: a `BeatPlan` with an `intent`, a `framing`, and one to `max_clips_per_beat` `ClipSpec`s, each with its own action, end state, camera motion, sound, `on_screen` list and `entity_ops` (`schemas.py:61-92`). The engine emits the clips one per segment with no LLM call (`emit`, `director_engine.py:237-314`), applying each clip's ops when that clip is emitted (`apply_clip`, `validator.py:115`), so a beat cut after clip *k* leaves the world at clip *k*. The beat's thread ops, voice-over and viewer disposition ride on clip 1 only (`validator.py:231-269`; `director_engine.py:264`).

Narratively a beat is "one action or one state" (`director.system.md:1`), and the discipline section asks the Director to let states last and to continue by default (`:64-65`). Session A shows this working at the level it was meant for: the character's `now` line persisted across beats (lying on his back from segment 31 through 33; the crown's holder through segments 53-64), and the "he lies still, nothing else happens" style of clip appears.

### 3.2 Why one to four, and whether four is principled

The 2026-09-17 investigation found that a Director call per clip put a 6.6 s call on a 6 s clip's critical path and that per-clip prompting made every clip an event. The beat was the answer to both: fewer calls, and a unit of intent longer than a clip. The cap of four came with it, undocumented beyond "up to this many" (`config.py:96`).

Is four principled? No, and it does not need to be, because it is not binding. Session A: 28 beats of two clips, 3 of three, 2 of one, none of four. The prompt's "most beats are two or three clips" (`director.system.md:48`) is what the model follows. The cap does three things: it bounds how far ahead the Director commits end states with fixed information (24 s at four clips), it bounds what `check()` validates (`validator.py:94`, only the first `max_clips`), and it bounds wasted output when the viewer cuts a beat. Any value from three to six would do the same. It is a latency and commitment compromise, not a derived constant.

Would more clips per beat help? The original reason (LLM calls on the critical path) is gone: the priority tier made the call 3 s (session A) and the loop renders just in time, so with two-clip beats there is one call per 12 s of film and 9 s of slack. Coherence: no evidence either way; the 2026-09-17 investigation's complaint was about per-clip resets, which two-clip beats already remove. Interactivity: a longer beat costs nothing in steering latency, because `abort_beat` drops unrendered clips and the line lands on the next clip that is not yet rendering (`runner.py:539-543`); the queue depth (`lead_clips` plus the clip in flight) is what sets the N+2 or N+3 delay. The cost of a long beat is wasted Director output when it is cut, and a less informed end state for the later clips. Classification: **keep**, with the guidance expressed in seconds rather than clips (E3) so it survives a change of `clip_seconds`.

### 3.3 Four concepts that the code keeps separate

| Concept | Unit | Set by | Consumed by |
|---|---|---|---|
| Clip duration | seconds (6 in the live config) | `clip_seconds`, else `i2v_duration` (`director_engine.py:92-94`) | the renderer (one compiled shape), the Director prompt, the pacing clocks (`EventRecord.duration`) |
| Clips per beat | count, 1-4 | the Director, capped by `max_clips_per_beat` | the engine's emit loop, `check()` |
| Scene duration | story seconds, target 240 | `scene_seconds`; `world.scene_seconds()` sums event durations since `scene_started` (`world.py:260-261`) | `scene_note` (75% and 100%), forced `scene_done` at 100% |
| Story progression | story seconds since a structural change | `world.last_change`, set by `apply_clip` when an entity is introduced or removed, changes holder, location or presence, or a thread opens or pays off (`validator.py:150,162,193,199,206,240,249,271-272`), and by a new scene (`:326`) | `pacing_note` (pressure at 1×, required at 1.5×), the Showrunner stagnation trigger at 1.5× |

A "segment" and a "turn" are the same thing as a clip in this engine; the words survive from the previous one. Note that posture, activity, position and a prop's `status` do **not** count as progression (`validator.py:166-177` append to `changes` without setting `r.changed`), which is the intended meaning of "structural" but is documented only inside the pacing text. Opening a thread does count, which gives the Director a cheap way to relieve pressure: in session A the pressure that built from segment 16 was cleared at 20 by `thread t1 opened` (transcript line 810 shows "no pacing pressure" at 22).

---

## 4. The mechanisms, one by one

Format for each: the problem it solves; the implementation path and consumers; whether it works on the evidence; hidden coupling; failure modes; where it belongs; classification.

### 4.1 Typed world model and entity state

**Problem.** Prose is lossy. The previous engine tracked props in a rewritten paragraph and lost them; characters had no state, so every clip restarted from the narration.
**Path.** `WorldModel` (`world.py:135-155`) holds `Entity` (`:31-71`) with `status`, `location`, `holder`, `present`, `gone`, `first_seen`, `last_seen`, `notes`, and for characters `posture`, `activity`, `position` (`:50-52`), summarised by `now()` (`:57-59`). Only `apply_clip` and `apply_showrunner` mutate it. Consumers: `format_world_now` (`context.py:80-129`), the compiler (`describe`), the transcript, persistence (`world.py:279-302`).
**Works?** Yes. Session A tracked a monster's removal, a princess's introduction, a crown changing hands and being worn, and the character's posture through 64 clips with no warnings. The 2026-09-17 run tracked a brick on a foot for twelve clips.
**Coupling.** `present` is set true by `on_screen` (`validator.py:213-224`) and false only by an explicit op or `remove`. Nothing decays it. `last_seen` is the only recency signal and drives the sort in `format_world_now` and the dormant list. `first_seen` is written here and read by the compiler (§4.13, §7.3).
**Failure modes.** A prop the Director stops listing stays "present" until an op says otherwise, so "World now" can accumulate stale presence over a long run (not observed in 64 clips; a latent risk for a 30-minute film). `position` mixes frame position and relative position ("close to the princess"); harmless for the prompt, meaningless for code.
**Belongs.** Deterministic code.
**Class.** **Keep.** Experiment E7 on presence decay before adding one.

### 4.2 Cast lock

**Problem.** "Only one character" is a rule LLMs break under narrative pressure (the 2026-09-08 review's rule-adherence sources).
**Path.** `Story.cast_locked` → `WorldModel.cast_locked` (`world.py:139`). `check()` makes an `introduce` of `kind == "character"` a hard violation (`validator.py:57-62`), which earns one retry (`director.py:114-136`); `apply_clip` skips it (`validator.py:130-131`). The cast sheet also says it in prose.
**Works?** Never fired in session A. The Director complied through the prompt: "A monster cannot be a new minifigure, but a monster-shaped LEGO prop appears" (transcript line 1296). It then built a princess out of the monster's bricks that the character kissed and crowned. Structurally a prop; narratively a second figure. The lock cannot see that, and it is not meant to.
**Coupling.** The retry is a second Director call on the critical path (3 s at priority tier) for something `apply_clip` would drop anyway. The retry's value is that the action gets rewritten around the missing character; a silent drop would render an action that names a figure the world does not have.
**Failure modes.** The creature-as-prop hole above; a story with `cast_locked: false` has no equivalent rule for "no *new* named characters after scene 3", which a longer story may want.
**Belongs.** Deterministic code for the structural check; the prompt for the spirit.
**Class.** **Keep.** Consider warning-only (no retry) only if live runs show retries (E6).

### 4.3 Mute-character enforcement

**Problem.** A `speaks: false` character must not get dialogue, and the video model would render whatever dialogue the prompt carries.
**Path.** `Entity.speaks` from the blueprint (`world.py:177`). `check()`: a hard violation (`validator.py:98-103`). `apply_clip`: filters `clip.dialogue` in place (`:226-229`) before the compiler reads it (`compiler.py:108-113`), so no dropped line reaches the video prompt. The cast sheet adds "Never speaks" (`context.py:31-32`).
**Works?** Never fired in session A. Same trade as the cast lock: a retry costs a call, and it buys a beat that does not depend on a line the character cannot say.
**Coupling.** Dialogue and voice-over are different channels: the voice-over is the narrator's (`BeatPlan.voiceover`), gated by pacing, never by `speaks`. The Lego blueprint's constraint "no dialogue, no voiceover" is contradicted by its own `tone_guidelines`, and the system honours the tone guidelines: 16 voice-over lines in session A. That is a blueprint inconsistency, not an engine one.
**Belongs.** Code.
**Class.** **Keep.**

### 4.4 Location resolution

**Problem.** The Director writes "at the white void" or "in The White Void" for a location id.
**Path.** `resolve_location` (`validator.py:33-49`) strips prefixes, tries `world.resolve`, then a substring test of the id in the slug. Used by `_check_op` (a warning, never a violation, `:76-82`) and `apply_clip` (`:194-202`); a protagonist move flips `present` on every location (`:200-202`).
**Works?** Untestable on this story, which has one location and forbids moving. `test_story.py:96-102` covers the prefix forms.
**Coupling.** Prop `location` is set to the current location on introduction (`:142-144`); `location` on ops is otherwise nearly inert in a one-location world.
**Failure modes.** The substring fallback can match the wrong id if one location's id is a prefix of another's (`void` in `void_2`). Untested on a multi-location blueprint.
**Belongs.** Code.
**Class.** **Keep but document** as unexercised; test before the first two-location story.

### 4.5 Scene cards

**Problem.** Without a goal and an exit, a film of "whatever comes to mind" holds one state indefinitely (the 113 turns of one apple in the 2026-09-08 review).
**Path.** `SceneCard` (`world.py:91-98`) written by `apply_showrunner` when `replan` or forced (`validator.py:313-329`); rendered with timing by `format_plan` (`context.py:42-66`) for both roles; the Director sets `scene_done`/`needs_replan`; the engine forces `scene_done` at the length cap (`director_engine.py:154-157`).
**Works?** As a menu, yes (§2.2). As a scene *boundary*, no: in 64 clips the Director never set `scene_done`, the length cap never fired (scene 1 ended at 222 s of 240), and both scene changes were `needs_replan` beside a viewer line. Scene 2's exit condition, "completes a sequence of experiments with the new prop, establishing its comedic potential," was arguably met by segment 56 and never declared.
**Coupling.** A new card resets `scene_started` to spawn+1 (`validator.py:325`), clears momentum (`:327-328`), bumps `last_change` (`:326`). `intensity` is shown and never consumed by code.
**Failure modes.** Staleness by construction (§5.1) and lost replans (§5.2). Candidates written for a world that no longer exists pull the Director back to them after each viewer excursion.
**Belongs.** The card is LLM content; its freshness and its lifecycle belong to code, and that is where the defects are.
**Class.** **Keep, redesign the handshake** (§9).

### 4.6 Story arc

**Path.** `Plan.arc` and `current_act` (`world.py:101-104`), rewritten wholesale by every Showrunner run that returns a non-empty arc (`validator.py:308-311`), shown to the Director with a marker (`context.py:44-48`).
**Works?** Written once at bootstrap in session A, unchanged across seven runs; `current_act` moved from 0 to 1. Cost: 3 lines, about 60 tokens per call.
**Failure modes.** None observed. It is cheap orientation. It also carries the Showrunner's only long-horizon intent between runs, since each run is stateless.
**Class.** **Keep but document** (that it is orientation for both roles, not a plan the code enforces).

### 4.7 Threads, dormant props, callbacks

**Problem.** Setups should pay off; props should return. This is what makes the film "one film".
**Path.** `Thread` (`world.py:74-80`), opened and paid off by the Director's `thread_ops` on clip 1 (`validator.py:231-249`), dropped by either role (`:301-306`); shown as the last 8 open (`context.py:132-140`) or 12 for the Showrunner. Dormant props: `present == False and not gone` (`world.py:218-219`), listed with `last_seen` (`context.py:118-123`) and in the stagnation report (`pacing.py:85-87`).
**Works?** Partly. Session A: four threads, one paid off (the standoff, 35→48), two gags open since 20 and 28 and never touched again, the Showrunner never dropped one. No dormant prop was called back (the monster was removed, not left dormant). The mechanism is sound; the Showrunner's pruning instruction did nothing.
**Coupling.** Opening a thread counts as progression (§3.3). A callback's prompt gets the prop's name only (§4.13).
**Failure modes.** Threads accumulate; the Director sees the eight most recent, so old ones vanish from view while staying open. A dormant prop's look never reaches the renderer on its return.
**Belongs.** Code for the ledger, LLM for the judgment.
**Class.** **Keep; simplify** the prompt side (show age, tell the Showrunner to drop threads older than N scenes) and **fix the callback look** in the compiler.

### 4.8 Pacing pressure (stagnation ladder)

**Path.** `pacing_note` (`pacing.py:43-60`): nothing below `stagnation_seconds`, "land a development" up to 1.5×, "REQUIRED NOW" above. Shown in the gates section. Separately, `_trigger` spawns a Showrunner replan at 1.5× when the gap allows (`director_engine.py:351-353`).
**Works?** Session A hit the first rung at 96 s (segment 16) and was cleared at 20 by a thread open beside the viewer's line. Never reached "REQUIRED". The Showrunner trigger never fired.
**Coupling.** Same threshold for the Director's demand and the Showrunner's replan: they act on the same plan, and the Showrunner acts on a snapshot that predates the Director's fix (§5.5). Progression is defined by the validator, not by pacing.
**Failure modes.** The double action above; the thread-open escape hatch. `ARCHITECTURE.md` still says the required shift is at twice the threshold; the code says 1.5× since `1de34a0`.
**Belongs.** Code, as a deterministic signal. The wording is prompt.
**Class.** **Keep; simplify** to one owner per rung: the Director gets one beat at "REQUIRED" before the Showrunner is asked (e.g. Showrunner at 2×).

### 4.9 Scene-length note and forced closure

**Path.** `scene_note` (`pacing.py:63-76`) at 75% and 100% of `scene_seconds`; `plan()` forces `scene_done` at 100% (`director_engine.py:154-157`), which makes `_trigger` return a replan on every plan until a new card lands.
**Works?** Neither rung fired in session A (scene 1 reached 222 s). The mechanism is the hard cap that keeps a scene from running all night, and it re-fires by construction (the scene is still over at the next plan), so unlike the Director's own flags it survives the hold.
**Failure modes.** Between the forced flag and the landing the Director is told "past its intended length" for two or three plans; harmless. The new scene's clock is backdated to the spawn segment, so clips rendered under the old card count toward the new scene.
**Class.** **Keep.**

### 4.10 Viewer interventions, deferred requests, viewer momentum

**Path.** `Intervention` (`world.py:83-88`); `add_intervention` in `plan()` (`director_engine.py:128-130`); the disposition applied on clip 1 to every pending item (`validator.py:251-261`); an absorbed/declined beat with nothing pending resolves the most recent *deferred* item (`:262-269`); `format_interventions` shows the current line, momentum, up to four deferred, the last three handled (`context.py:143-173`). Momentum: set in `emit()` on clip 1 when absorbed and a line was given (`director_engine.py:251-253`); cleared by any replan landing (`validator.py:327-328`); rendered when there is no current line (`context.py:154-161`).
**Works?** The ledger works: four lines, four correct dispositions, shown in the player. The 2026-09-17 loop's join-and-cancel path works (`runner.py:490-514`). Momentum has not run live.
**Coupling and failure modes.**
- **Double line (bug, reproduced).** A line that arrives after `_plan()` returned but before the fire moment goes through `abort_beat` (zero clips emitted) and a new `_plan(text)` with only the new text (`runner.py:534-543`). The earlier line stays `pending` at the same segment and is stamped with the second beat's disposition. Reproduction with the mock backend and the replay renderer: Director calls saw `(3, "LINE-A")` then `(3, "LINE-B")`; interventions recorded `LINE-A deferred, LINE-B absorbed`; history shows only LINE-B. The cancel path (`:506-513`) does it right; the post-return path does not.
- **Deferred resolution by position (bug).** `deferred[-1]` is resolved regardless of which request the beat delivered (`validator.py:263-266`). With two deferred lines the wrong one closes. The Director has no field to name the request it answered.
- **Momentum never decays** and is erased by a landing regardless of whether the landing's spawn predates the line (§5.4). It duplicates the "recently handled" list (same text) and the system prompt's "let it shape your beats for several turns" (`director.system.md:37`): three mentions of one fact.
- A deferred line later absorbed without new input sets no momentum (`_beat_input` is empty).
**Belongs.** The ledger and the race handling are code. Whether steering should persist is prompt policy; whether it should persist *through a scene change* is a product decision (Q1).
**Class.** Ledger **keep**; double-line and deferred-by-position **fix**; momentum **experiment first** (E1), then redesign its reset scope.

### 4.11 Compaction, summaries, entity notes, opening pin

**Path.** Triggered every `compaction_interval` segments since `last_compacted` (`director_engine.py:354-355`), or as part of any replan. The Showrunner reads events since `last_compacted` (`showrunner.py:89`; `context.py:222-238`) and returns `scene_summary`, `act_summary`, `entity_notes`; `apply_showrunner` stores a `Summary(scene, last_compacted+1 .. at_segment)` and advances `last_compacted` (`validator.py:282-289`), replaces the act summary, appends deduplicated notes (`:294-299`). `format_summaries` pins `scenes[0]`, then the act summary, then the last three (`context.py:186-200`). Notes: the last four per character in "World now" (`context.py:101-102`).
**Works?** The summaries in session A are accurate and compact (231-505 characters each). The act summary is a good one-paragraph state of the film.
**Coupling and failure modes.**
- **One clip lost per window (bug).** The run is spawned in `plan()` before `emit()` applies that segment's clip (`director_engine.py:164-172` precede the emit), so the snapshot's events end at `at_segment - 1`, but the summary is labelled through `at_segment` and `last_compacted` is set to it. The next window starts at `at_segment + 1`. Session A: the "11-20" summary has no breakdance, though clip 20 is the absorbed breakdance line and the `t1` thread open; the "32-35" summary has no monster, though clip 35 introduced it; "36-46" then opens with the monster "appearing", inferred from later events. The lost clip is always the first clip of the triggering beat, which is the clip that carries the beat's viewer disposition and thread ops.
- "Scene" summaries are compaction windows, not scenes (windows 1-10, 11-20, 21-31, 32-35, 36-46, 47-56 against scenes 1-37 and 38-64). The pinned "opening" is the first window.
- **Prop notes are dead data.** `format_world_now` prints notes only in the character branch; a prop's notes (three in session A) are stored and shown to nobody, including the Showrunner that wrote them, which can only avoid exact duplicates (`validator.py:297`).
- Character notes beyond four leave the prompt silently (seven on the protagonist by segment 64). The bootstrap note restated the blueprint verbatim, which the prompt forbids (`showrunner.system.md:31`), and it sat in every Director prompt until note five pushed it out. Most notes duplicate the summaries ("Has tested jumping, sitting…").
**Belongs.** Code for windows and storage; LLM for the text.
**Class.** **Keep**; **fix** the window boundary; **simplify** notes (cap per entity, reject a note contained in the cast description, drop prop notes or show them).

### 4.12 Context budgets and `DESCRIBED_IN_FULL` versus `PRESENT_IN_FULL`

**Path.** `PRESENT_IN_FULL = 12`, `STATUS_CHARS = 120`, `GONE_LISTED = 12`, `NOTES_SHOWN = 4` (`context.py:69-72`), threads 8/12, summaries 3/6, `recent_clips` 6. `DESCRIBED_IN_FULL = 6` (`compiler.py:63,94-95`).
**Assessment.** Different consumers with different budgets: twelve entity lines for a writer, six described entities for a video model whose prompt should stay short. Not a mismatch. `DESCRIBED_IN_FULL` is nearly inert: on a continuing clip `describe(full=False)` returns a prop's name anyway (props have no `look_short`), so the cap only limits characters' short looks past the sixth and same-clip introductions past the sixth. All caps were far from binding in session A (three present entities at most).
**Class.** **Keep but document** (one comment naming the two consumers).

### 4.13 The compiler's dependence on `first_seen == segment_no`

**Path.** `describe(e, full=opening or e.first_seen == segment_no)` (`compiler.py:94`): the full look on the opening clip or on the clip that introduced the entity; otherwise `look_short` or the name.
**Assessment.** The audit files this under resume. Resume is unreachable (§7.2). The real failure is different and observed: an entity introduced by an op but not listed in `on_screen` for that clip never gets its description into any video prompt. Session A, segment 53: `introduce prop princess_crown (A bright, blocky LEGO crown…)` applied, but `on_screen` was `[lego_man, lego_princess]`, so the compiled prompt reads "On screen: Lego Man (…); lego princess." and every later clip says "princess crown" with no look. The same holds for a dormant prop returning after many clips: the sliding window no longer carries it, and the prompt names it only. The correct condition is "the entity was not in the previous clip's picture", not "it was introduced this segment".
**Class.** **Fix** (small: track the previous clip's `on_screen` or use `last_seen < segment_no - 1`).

### 4.14 No-disposition correction and other validator fix-ups

`intervention == "none"` with a pending line becomes `deferred` with a warning (`validator.py:254-256`); id-like names get spaces (`:134-135`); a character's `holder` is ignored with a warning (`:178-180`); an `introduce` of an existing entity is treated as an update (`:56`); unknown `on_screen` references are dropped (`:104-106`); thread ops on closed threads are ignored (`:108-111`). All are the right shape: silent fix-ups for things a retry would not improve, hard violations for things it would. None fired in session A. **Keep.**

---

## 5. Conflict and failure-mode analysis

### 5.1 Stale snapshot: the Showrunner plans without the beat that triggered it

`_spawn_showrunner` deep-copies the world at the end of `plan()` (`director_engine.py:358-361`), after `add_intervention` and before `emit()`. The snapshot has the viewer's line as `pending`, no clip of the new beat, and events through the previous segment. Session A, segment 35: the Director absorbed "a monster shows up and he needs to fight", introduced the monster in clip 1 and set `needs_replan`. The Showrunner, spawned in that plan, saw a world with no monster and a pending request, and wrote scene 2: "Lego Man discovers his first prop in the white void… A simple prop (e.g., a LEGO soccer ball or a block) appears nearby". It landed at 38 with a monster three clips on screen. This is the same defect as the compaction gap (§4.11): the run should be spawned after clip 1 is applied, or the planned beat should be in its context.

### 5.2 A held one-shot trigger is dropped

`_trigger` reads the current beat's `scene_done` and `needs_replan` (`director_engine.py:347-350`). `_hold_reason` returns why a run cannot start (`:383-397`) and `plan()` only writes a note (`:167-170`); nothing remembers the trigger. The docstring and the config comment say a held trigger "is looked at again next segment" (`config.py:99-102`; `:383-386`). That is true only for triggers derived from world state (stagnation, compaction, the forced length cap). Session A, segment 48: the Director absorbed "they start kissing and the monster turns", removed the monster, introduced a princess and set `needs_replan`; the hold said "2 segment(s) since the last run, minimum 4". Beat 26 at segment 50 carried no flag. The compaction at 56 landed with `replan = false` ("no structural change is needed mid-scene"). The princess and crown story ran its remaining sixteen clips under the "first prop" card of §5.1.

### 5.3 Landing granularity is a beat, not a segment

`_land_if_done` runs only inside `plan()` (`director_engine.py:123`, `:389`). A finished run waits for the next Director call: up to one beat (12 s at two clips, 24 s at four). Session A's landings were 2, 2, 1, 3, 1 and 2 segments after their spawns. `CLAUDE.md` and `ARCHITECTURE.md` say "the first segment"; the audit (3.2) says "the first plan()", which is right. Not a bug; a documentation error and a small latency the beat design introduced.

### 5.4 Momentum is erased by the event it caused, and never otherwise

A replan landing clears momentum unconditionally (`validator.py:327-328`). The typical replan is spawned by the Director's `needs_replan` beside an absorbed line, so the line sets momentum on clip 1 and the landing two segments later erases it, while the new card (written on the stale snapshot, §5.1) may not know the line happened. A stagnation replan spawned before a line and landing after it does the same. Between landings, momentum has no expiry: a line from five minutes ago still reads "this energy should still shape your beats". Unverified live (the feature is newer than session A), but it follows from the code. A landing should clear momentum only if its spawn segment is later than `viewer_momentum_segment`, and momentum should age out in story seconds.

### 5.5 Stagnation double action

The Director's "REQUIRED NOW" and the Showrunner's stagnation replan share the 1.5× threshold (`pacing.py:55`, `director_engine.py:352`). On the plan where both fire, the Director introduces something (clearing `last_change`) and the Showrunner, on a snapshot that predates it, writes a card to introduce something else, landing a beat or two later and clearing momentum. Not observed (session A never reached 1.5×). Give one owner a head start.

### 5.6 Two viewer lines within the slack window

Reproduced (§4.10). Consequence: the first line's text never reaches the Director, its disposition is whatever the second beat said, and a Director call is wasted. Window: the time between a Director return and the just-in-time fire moment, a few seconds per beat on the priority tier.

### 5.7 Candidate beats as gravity

Not a defect in code, but the strongest conflict in the story. With a fresh card, the Director's return to the candidates after a viewer excursion is the intended "the plan bends, then the film goes on". With a stale card (§5.1, §5.2) it is the film reverting to a premise the viewer already changed. The system prompt's "a request outranks the scene card" resolves the conflict only for the beat that receives the line.

### 5.8 Runtime edges (noted, owned by Prompt 03)

An exception in `emit()` is caught by the producer's `except Exception` and stops the film (`runner.py:748-750`); there is no per-clip recovery. A failed render is skipped (`:451-455`, `:631-652`) *after* `apply_clip` has advanced the world, so the world can be one clip ahead of what the viewer saw; the next prompt continues from a picture that never played. Both are policy questions (Q4), not validator questions.

### 5.9 What is not a conflict

The Showrunner never blocks a segment (verified by `test_showrunner_never_blocks_a_segment` and by session A's notes). Two Director calls cannot overlap. The validator cannot be out of date with respect to the world because it is the only writer. The Showrunner's summaries are never contradicted by later state because they are text about the past.

---

## 6. Context and prompt size

### 6.1 Director prompt composition

Reconstructed from session A's saved world at segment 65 (three entities present, four threads, six summaries, seven notes on the protagonist). Tokens are o200k.

| Part | Segment 1 | Segment 65 | Moves? |
|---|---|---|---|
| **System prompt** | 2,811 | 2,811 | never (cache-stable; notes are in the user prompt) |
| … instructions (template text without placeholders) | 1,818 | 1,818 | |
| … world constraints | 359 | 359 | |
| … cast sheet | 197 | 197 | |
| … visual style | 146 | 146 | |
| … tone guidelines | 112 | 112 | |
| … locations | 99 | 99 | |
| … premise, title | 78 | 78 | |
| **User prompt** | 229 | 2,001 | every call |
| … recent clips (6) | 10 | 656 | |
| … story so far (pin + act + 3) | 14 | 429 | |
| … plan (arc + card + 5 candidates) | 13 | 319 | |
| … world now | 44 | 256 | |
| … open threads | 4 | 111 | |
| … gates | 53 | 66 | |
| … viewer (no line / with a line) | 7 | 66 / 106 | |
| … where the picture is | 21 | 36 | |
| … headings | 61 | 61 | |
| **Total input** | 3,040 | 4,812 | |
| `BeatPlan` JSON schema (sent with every strict call) | 1,114 | 1,114 | never |

The 2026-09-17 traces measured about 4.2K input tokens with 2.9K served from cache for the previous engine, which matches: the system prompt is the cached part. Session A's Director calls averaged 2.98 s at about 330 output tokens on the priority tier; input size is not the latency term.

### 6.2 Showrunner prompt composition (compact mode, segment 65)

| Part | Tokens |
|---|---|
| System prompt | 1,490 (671 instructions, 359 constraints, 197 cast, 99 locations, 90 blueprint narratives, 78 premise) |
| Events since last compaction (8 clips) | 841 |
| Story so far (pin + act + 6) | 594 |
| Plan | 319 |
| World now | 256 |
| Viewer requests (all, up to 12) | 154 |
| Threads (12) | 111 |
| Instruction | 92 |
| Stagnation report | 32 |
| **Total** | 3,945 |
| `ShowrunnerUpdate` schema | 459 |

The events section is 105 tokens per clip. That is the number compaction saves the Director every call.

### 6.3 The boilerplate claim

The 61% figure comes from the 2026-09-17 investigation, §4, and measured the **compiled video prompt** under the previous compiler, which repeated the 112-word style paragraph and the full character look on every clip. The audit's improvement 16.5 attributes it to the Director prompt; that is a misreading. The current compiler sends the full style, full looks and the reference pose only on the opening clip (262 words in session A) and the one-sentence `visual_anchor` plus `look_short` on continuing clips: 118 words on average (94 to 156), of which 54 words, 46%, are that fixed anchor. The same investigation's eight-clip probe without any anchor lost framing and grew eyebrows by clip six, so the anchor is a measured need, not boilerplate. The right length is still an experiment (E9), unchanged from the 2026-09-17 recommendation.

For the Director prompt, "boilerplate" in the sense of unchanging text is the 2,811-token system prompt. It is cached and it is what makes the Director story-agnostic; its 1,818 instruction tokens are dense with rules the 2026-09-17 investigation added on purpose (continue by default, let things last, one new thing per beat). There is no case for cutting it for speed.

### 6.4 What is repeated, what is stable, what is needed every call

**Repeated within one call.**
- The last clip's end state: in "Where the picture is" (`_beat_status`), as the last recent clip's "ends on", and implicitly in the character's `now` line. Three places, one fact.
- The viewer's last absorbed line: momentum, "Recently handled", and the system-prompt rule about letting direction persist. Three places.
- The opening pin and the act summary overlap by construction (the act summary is written with the opening in view).
- Entity notes restate the summaries (session A's seven notes are all of the form "has tested X").
- The card's five candidate beats, about 150 tokens, are re-sent on every call whether the viewer is active or not.

**Stable, and rightly so.** The system prompt. The `BeatPlan` schema (1.1K tokens per request; whether OpenAI bills it as prompt tokens is not visible in the traces and is worth one look).

**Needed every call.** World now (the only truth about state), the card's goal, tension and exit (about 120 tokens without candidates), the viewer section, the gates, where the picture is, and *some* recent clips. Six recent clips at two-clip beats is the last three beats, 36 s of film, 656 tokens; the rule they serve is "do not restart a bit that just ended" (`director.system.md:38`). Three clips would cover the previous beat and a half.

**Reductions, each tied to a hypothesis and a risk.**

| Reduction | Saves | Hypothesis | Risk |
|---|---|---|---|
| Recent clips 6 → 3 | ~330 tok | The `now` line and the end state already carry continuity; the extra clips add little. | The Director restarts a bit that ended 20 s ago. Measure bit-restart rate (E4). |
| Candidates only when no viewer momentum is active | ~150 tok on active turns | Candidates are for the idle film; during a viewer excursion they pull the Director back (§5.7). | The film stalls when the excursion runs out. (E5) |
| Drop the momentum block, keep "recently handled" and the rule | ~40 tok | Three mentions of one fact are two too many. | Steering snaps back sooner. This is E1, run before deciding. |
| Drop prop `looks` from world now | ~20 tok per prop | The Director does not need the renderer's description. | None found; the compiler reads `look` from the model, not the prompt. |
| Show the opening pin only until the act summary exists | ~40 tok | The act summary subsumes it. | The opening is the thing viewers refer back to (the pin's stated reason). |

None of these is worth doing for latency. They are worth doing if E1, E4 or E5 show a behavioural gain.

---

## 7. Known suspicious behaviour: bug or valid design

| Item | Verdict | Detail |
|---|---|---|
| 7.1 `viewer_momentum` reset scope | **Design defect, unverified live** | Cleared by any replan landing regardless of spawn time; no decay (§5.4). Small deterministic fix once E1 says momentum stays. |
| 7.2 Non-persisted `StoryState.history` | **Moot; dead code** | `_init_world` loads `session_dir/world.json` if present (`director_engine.py:323-326`), but `run_live` and `run_play` always create a fresh timestamped session directory (`runner.py:52-53`, `:377`), so resume never happens. The audit's resumed-voice-over concern cannot occur. Either remove the branch or build resume properly (then persist history or read `EventRecord.voiceover`, which is already saved). |
| 7.3 Compiler and `segment_no == first_seen` | **Bug, observed** | Not about resume: a prop introduced without being on screen, or returning from dormancy, never gets its look into a prompt (§4.13; session A segment 53). |
| 7.4 `i2v_duration` versus `clip_seconds` | **Valid design, one owner too many** | The engine owns the value (`clip_seconds` property, `director_engine.py:92-94`) and writes it into `Shot.duration_seconds`. The runner recomputes `clip_hold` from the same two fields (`runner.py:461`) and falls back to `i2v_duration` alone when a probe fails (`:446-448`, `:654`), which gives 5 for a 6 s clip. `Shot.duration_seconds` also caps at 15 (`responses.py:31`). Ownership should be the engine's alone; the runtime policy is Prompt 03's. |
| 7.5 Legacy `data/` path | **Cleanup** | `data/story.json` differs from `stories/lego-void/story.json` in two lines (no `seed_image`; `reference_image: "data/legoman.png"`); the tests load `data/story.json` (`tests/test_story.py:26`, `test_engine.py:46`); `Config.i2v_seed_image` defaults to `data/legoman.png` (`config.py:45`) and `main.py:67-68` overrides it from the story; `ARCHITECTURE.md` still names `data/story.json`. `Character.reference_image` is loaded into `Entity.reference_image` (`world.py:178`) and read by nothing (the renderer uses `config.i2v_seed_image`); only `reference_pose` is used. |
| 7.6 Narration pause defaults | **Inconsistent default, valid behaviour** | `Config.min_pause_seconds` is 5.0 (`config.py:81`); every shipped config sets 15. `StoryState` has no such field (the audit's wording is off). With 12 s beats the gate is evaluated once per beat, so a 15 s floor means a line on every other beat, and the Director filled 16 of the roughly 17 open gates in session A. "Empty is the normal choice" (`director.system.md:56`) is not what the model does; the gate is the only limiter. Density is a product choice (Q5). |
| 7.7 Stale runner views if `emit()` fails | **Not a failure mode; the real one is fail-stop** | `emit()` writes views after apply and compile (`director_engine.py:265`); an exception anywhere in it propagates to the producer's handler, which sets `stop_event` and ends the film (`runner.py:748-750`). `test_web.py::test_player_input_joins_lines_and_pipelined_items_keep_views` covers the pipelined case. The question is whether a 30-minute film should end on one exception (Q4). |

---

## 8. Feature decision matrix

| Mechanism | Classification | Confidence | Evidence |
|---|---|---|---|
| Two roles, two clocks | keep, clarify authority, redesign the handshake | high on keep, medium on the shape of the fix | §2, §5.1-5.4; 2026-09-17 measurements; session A |
| Synchronous bootstrap | keep but document | medium | one call, ~3.7 s, during lead build; the Director never sees `short_term_narrative` |
| Beats of 1-4 clips | keep; guidance in seconds (experiment) | medium | 28/33 beats of two clips; cap never hit |
| Typed world model + character state | keep | high | session A, 2026-09-17 run |
| Cast lock | keep | high | never fired; prompt compliance; creature hole is by design |
| Mute enforcement | keep | high | never fired; drop-before-compile is correct |
| Location resolution | keep but document (untested) | medium | one-location story |
| Scene cards | keep, redesign the handshake | high on the defects | §5.1, §5.2; `scene_done` never set |
| Story arc | keep but document | medium | unchanged in seven runs; 60 tokens |
| Threads | keep, simplify prompting | medium | two gags open 36-44 segments; Showrunner never pruned |
| Dormant props / callbacks | keep; fix the returning look | high on the bug | segment 53 |
| Stagnation ladder | keep, simplify thresholds | medium | §5.5; thread-open escape hatch |
| Scene-length note + forced closure | keep | medium | never fired in 64 clips; re-fires by construction |
| Intervention ledger | keep | high | four correct dispositions |
| Double-line handling | fix | high | reproduced |
| Deferred-by-position resolution | fix | high | code reading |
| Viewer momentum | experiment first, then redesign reset | medium | duplicated signal; unverified live |
| Compaction and summaries | keep; fix the window gap | high on the bug | three summaries missing their spawn clip |
| Entity notes | simplify | medium | blueprint restated; prop notes dead; four-note window |
| Opening pin | keep but document | low stakes | it is the first window, not the opening scene |
| Context caps | keep but document | high | non-binding; two consumers |
| No-disposition and other fix-ups | keep | high | correct shape |
| Resume branch in `_init_world` | remove or build | high | unreachable |
| `data/` legacy, `Entity.reference_image` | remove | high | two-line diff; dead field |

---

## 9. Recommended target architecture

**What remains.** Two LLM roles on two clocks over a typed world model. The Director as the only call on the critical path, one per beat, returning clips with per-clip ops. The Showrunner as memory (compaction, notes, pruning) and as the idle author (arc, card, candidates), never blocking. The validator as the only writer. The compiler as the only place that knows the video model. The live loop's just-in-time render and interrupt-on-input. The pacing clocks in story seconds. Persistence per clip.

**What changes, all small and deterministic.**

1. **Trigger memory.** `_hold_reason` records a held `scene_done` or `needs_replan` on the engine; `_trigger` re-raises it at the next plan until it spawns. (§5.2)
2. **Spawn after the first clip.** Evaluate the trigger in `plan()` as now, but spawn the task from `emit()` of clip 1 after `apply_clip`, so the snapshot holds the beat's first ops and the resolved disposition, and the compaction window includes its spawn clip. (§4.11, §5.1)
3. **Landing guard for viewer direction.** A landing clears momentum only if `at_segment >= viewer_momentum_segment`; momentum ages out after a configurable number of story seconds. Only if E1 keeps momentum. (§5.4)
4. **Join the second line.** In `_listen`, when a line arrives after a plan has returned and before the fire moment, treat it like the cancel path: discard the pending intervention, join the texts, plan once. (§5.6)
5. **Resolve deferred requests by identity.** Either resolve all deferred items on an absorbed beat, or add the request's segment to `BeatPlan` so the Director names what it delivered. (§4.10)
6. **Full look on re-entry.** `describe(full=…)` when the entity was not in the previous clip's picture, in addition to opening and introduction. (§4.13)
7. **One owner per stagnation rung.** Director's "REQUIRED" at 1.5×, Showrunner replan at 2× (or one beat later). Update `ARCHITECTURE.md`. (§5.5)
8. **Notes.** Cap per entity, reject a note that is a substring of the cast description or a summary, and either render prop notes or stop collecting them. (§4.11)
9. **Cleanup.** Delete the unreachable resume branch or build resume; point the tests at `stories/lego-void`; delete `data/`; drop `Entity.reference_image` until per-character references are built; align `Config.min_pause_seconds` with the shipped 15; fix the "first segment" wording in `CLAUDE.md` and `ARCHITECTURE.md`; make the runner read the engine's `clip_seconds`.

**What might change after experiments.** Momentum on or off (E1). The Showrunner's planning half on or off while the viewer is active (E2, E5). The beat cap expressed in seconds (E3). Recent clips 6 or 3 (E4). Async bootstrap (E8). Voice-over density (E10).

**What should not change.** The strict schemas. The validator's split of hard violations versus warnings. The system prompt's stability. The single-writer world model. The beat as the unit of the Director's call.

---

## 10. Questions that need the user's judgment

1. **Should a viewer's direction survive a scene card written after it?** Today a replan erases momentum and a stale card pulls the Director back. The alternative is that the viewer's last direction outranks the card until it ages out, which makes the Showrunner's card advisory while the viewer is active.
2. **Is the Showrunner's planning half wanted while the viewer is active, or only as the idle author?** E2 measures the cost of switching it off; the answer decides how much of §9's handshake work is worth doing.
3. **How long should a viewer's direction last** when nothing else changes: a fixed number of beats, story seconds, or until the next line?
4. **Fail-stop or fail-soft?** An exception in `emit()` ends the film; a failed render leaves the world one clip ahead of the picture. Should the loop roll the world back on a failed render, or hold the last frame and retry?
5. **Voice-over density.** One line per two beats is what the current floor produces and the Director fills nearly every open gate. Is that the intended register, or should the floor be 30 s or more?
6. **The creature hole in the cast lock.** A princess built from bricks, kissed and crowned, is a prop to the validator. Is that acceptable for this blueprint, or should the blueprint say so explicitly?
7. **Bootstrap.** Is 3-4 s of startup worth an authored opening card, or should the blueprint's `short_term_narrative` be the opening card and the Showrunner's first run land asynchronously?
8. **Threads.** Should a thread expire by age or scene count, or is a stale open gag harmless?

---

## 11. Experiments needed before irreversible simplification

All can run headless with scripted lines (`scripts/steer.py`) on the mock or the real Director; E9 and E10 need the box.

| # | Experiment | Measures | Decides |
|---|---|---|---|
| E1 | Momentum on versus off, same scripted lines, 40 segments | beats until the Director returns to a card candidate after a line; viewer lines per scene needed to hold a direction | whether momentum earns its three mentions, and its reset scope |
| E2 | Showrunner memory-only (no card; the Director keeps its own goal in `intent`) versus full | does the film move when the viewer is silent; stagnation rungs reached; repeated bits | whether the planning half is needed at all |
| E3 | `max_clips_per_beat` 4 versus 6 with guidance "as long as the action lasts" | beat-length distribution, beats cut by the viewer, wasted output tokens, restart rate | the cap and its unit |
| E4 | `recent_clips` 6 versus 3 | bit-restart rate, prompt tokens | the biggest user-prompt section |
| E5 | Candidate beats hidden while momentum is active | snap-back rate versus stalls | §5.7 |
| E6 | Count validator violations across live runs | retries per 100 clips | whether cast lock and mute checks should stay hard |
| E7 | Presence decay off versus after N clips off screen, 200-segment headless run | props "present" but unseen; "World now" length | §4.1 |
| E8 | Async bootstrap with a blueprint-derived opening card | startup time; quality of beats 1-3 by transcript | §2.4 |
| E9 | Anchor length on a ten-clip continue chain on the box | framing and face drift | already recommended on 2026-09-17; unchanged |
| E10 | `min_pause_seconds` 15 versus 30 and the "empty is normal" wording | lines per minute; whether the Director ever leaves an open gate empty | §7.6 |

---

## 12. Audit-versus-code discrepancies

| # | Audit says | Code or evidence says |
|---|---|---|
| 1 | The beat engine is "not yet run live" (also `CLAUDE.md`, `README.md`) | `logs/live-box/director-ltxv_live_20260918_145552` is a 64-clip live run of it on commit `265e9fd`; momentum, the 1.5× thresholds and per-story folders (`1de34a0`) are what remain unverified |
| 2 | 16.5: "61% prompt boilerplate" applies to the Director prompt | The figure is the 2026-09-17 investigation's measurement of the compiled video prompt under the old compiler; today 46% of a continuing clip's video prompt is the anchor, by design |
| 3 | 5.44: a held trigger "is re-evaluated next segment" (also `config.py:99-102`, engine docstring) | Only state-derived triggers re-fire; `scene_done` and `needs_replan` are dropped (session A, segment 48) |
| 4 | `CLAUDE.md`, `ARCHITECTURE.md`: the update "lands at the start of the first segment" | It lands at the first *plan* (`_land_if_done` is called only in `plan()`), up to one beat later; the audit's 3.2 has it right |
| 5 | `ARCHITECTURE.md` §4: required shift "at twice" the stagnation threshold | 1.5× since `1de34a0` (`pacing.py:48`, `director_engine.py:352`); the audit says 1.5× |
| 6 | 15: stale runner views if `emit()` fails | An exception in `emit()` ends the film (`runner.py:748-750`); no stale views |
| 7 | 15 / 16.1: non-persisted history could produce an unwanted voice-over on resume | Resume is unreachable from `main.py`; the load branch is dead |
| 8 | 8.10: multiple lines are joined into one intervention | Only when queued together; a line arriving after a plan returned becomes a second intervention the Director never sees (reproduced) |
| 9 | 5.3: momentum "makes steering persistent" | Design intent; it has never run live; it is set only on `absorbed` with a current line, never for a deferred line absorbed later |
| 10 | 5.15: compaction "every `compaction_interval` segments" | Every `compaction_interval` segments *since the last landed summary*, which any replan also advances; and each window omits its spawn clip |
| 11 | 5.55: the opening scene summary is pinned | The first compaction window (segments 1-10) is pinned; scene 1 ran to 37 |
| 12 | 6.3: the Showrunner "does not see the Director's recent beats" | Events carry `beat_intent`, `clip_index`, `clip_count` (`context.py:203-205`) |
| 13 | 5.42: lenient location resolution "prevents false rejections" | An unknown location is a warning, never a rejection (`validator.py:76-82`) |
| 14 | 15: `min_pause_seconds`: "`StoryState` defaults to no specific pause" | `StoryState` has no such field; the default is `Config`'s 5.0 |
| 15 | 13.5: a failed render skips the clip | True, and the world has already advanced by that clip's ops |
| 16 | 4.1: system prompt placeholders | Correct; worth adding that `short_term_narrative` and `long_term_narrative` never reach the Director |
| 17 | 17 / `ARCHITECTURE.md`: the blueprint is `data/story.json` | `stories/lego-void` is the default (`main.py:29`); `data/` is a two-line-different copy used by the tests |
| 18 | 5.16: dormant props can be brought back | They can, but the returning prop's look never reaches the video prompt |
| 19 | 5.56: `NOTES_SHOWN = 4` per entity | Per *character*; prop notes are never rendered |

---

## 13. Repository citations

Engine: `src/graph/director_engine.py` — `plan` 113-174 (land 123, intervention 128-130, bootstrap 132-135, gates 137-139, Director call 141-145, cap and voice-over 150-152, forced `scene_done` 154-157, trigger and hold 164-172); `discard_pending_input` 176-180; `abort_beat` 182-212; `_beat_status` 214-221; `_fallback_beat` 223-233; `emit` 237-314 (momentum 251-253, compile 254-262, event 284-300); `_init_world` 322-332; `_trigger` 343-356; `_spawn_showrunner` 358-371; `_run_showrunner_now` 373-381; `_hold_reason` 383-397; `_land_if_done` 399-418; `_land` 436-470; `_write_views` 474-510.

Agents: `src/agents/director.py` — `run` 66-136 (system placeholders 79-90, user sections 91-107, retry 114-136). `src/agents/showrunner.py` — `INSTRUCTIONS` 41-61, `run` 64-112 (system placeholders 73-82, events since last compaction 89).

World: `src/story/world.py` — `Entity` 31-71 (`posture`/`activity`/`position` 50-52, `now` 57-59); `Thread` 74-80; `Intervention` 83-88; `SceneCard` 91-98; `Plan` 101-104; `Summary` 107-111; `EventRecord` 114-132; `WorldModel` 135-155 (`viewer_momentum` 153-154); `from_blueprint` 161-184; `resolve` 196-209; `present_entities` 214-216; `dormant_props` 218-219; `drop_pending_interventions` 242-249; `scene_seconds` 260-261; `seconds_since_change` 267-268; `save`/`load` 279-302.

Validator: `src/story/validator.py` — `resolve_location` 33-49; `_check_op` 52-82; `check` 85-112; `apply_clip` 115-273 (introduce 129-150, remove 154-163, status 166-169, character state 170-177, holder 178-193, location 194-202, present 203-208, `on_screen` 213-224, dialogue 226-229, threads 231-249, interventions 251-269, `last_change` 271-272); `apply_showrunner` 276-333 (summary 282-289, notes 294-299, threads 301-306, arc 308-311, card 313-329, momentum 327-328).

Context: `src/story/context.py` — `format_cast` 23-34; `format_plan` 42-66; caps 69-72; `format_world_now` 80-129 (notes 101-102, dormant 118-123); `format_threads` 132-140; `format_interventions` 143-173 (momentum 154-161, deferred 162-168, handled 169-172); `format_interventions_all` 176-183; `format_summaries` 186-200; `format_recent` 208-219; `format_events` 222-238; `format_gates` 241-253.

Pacing and compiler: `src/story/pacing.py` — `voiceover_gate` 17-25, `tick_story_clock` 28-40, `pacing_note` 43-60, `scene_note` 63-76, `stagnation_report` 79-88. `src/story/compiler.py` — `describe` 48-60, `DESCRIBED_IN_FULL` 63, `compile_clip` 66-127 (on-screen 91-97, opening pose 101-105, style 123-127).

Schemas and prompts: `src/story/schemas.py` — `EntityOp` 23-44, `ClipSpec` 61-78, `BeatPlan` 81-92, `ShowrunnerUpdate` 108-119. `src/prompts/director.system.md` — reading the context 34-40, output 44-60 ("most beats are two or three clips" 48, voice-over 56), discipline 64-69. `src/prompts/showrunner.system.md` — principles 27-32.

Runner and player: `src/eval/runner.py` — `run_live` 327-833; `_plan` 490-514; `_listen` 516-554; producer loop 556-741; failure handling 744-756; `_playable` 836-853. `src/playback/player.py` — `next_input` 74-83.

Config and story: `src/models/config.py` — `clip_seconds` 44, `i2v_seed_image` 45, `min_pause_seconds` 81, `max_clips_per_beat` 96, `showrunner_min_gap` 99-102, `stagnation_seconds` 103. `src/models/story.py` — `reference_image`/`reference_pose` 26-27, `from_json` 45-60. `main.py` 67-68. `src/models/responses.py` — `Shot.duration_seconds` 31.

Tests: `tests/test_engine.py` (config 37-42, `data/story.json` 46), `tests/test_story.py:26`, `tests/test_live_timing.py`, `tests/test_web.py:151`.

Runs and reviews: `logs/live-box/director-ltxv_live_20260918_145552.md` and `logs/live-box/sessions/director-ltxv_live_20260918_145552/`; Langfuse session `director-ltxv_live_20260918_145552`; `docs/reviews/2026-09-17-continuity-and-buffering.md` §4 (video prompt composition) and §2 (Director latency); `docs/reviews/2026-09-17-rec1-dry-runs.md`; `docs/reviews/2026-09-08-orchestration-review.md` §2, §9.

---

## 14. Prioritised list

### Safe, obvious cleanup

1. Remember a held `scene_done`/`needs_replan` and re-raise it at the next plan (§5.2). Small; it makes the code do what its comments say.
2. Include the spawn segment's clip in the compaction window and the Showrunner snapshot: spawn from `emit()` of clip 1 after `apply_clip` (§4.11, §5.1).
3. Join a second viewer line that arrives after a plan has returned, as the cancel path does (§5.6).
4. Resolve deferred requests by identity, not `deferred[-1]` (§4.10).
5. Full entity look on re-entry in the compiler (§4.13).
6. Render prop notes or stop collecting them; reject notes that restate the cast sheet (§4.11).
7. Remove the unreachable resume branch or note it as unbuilt; point tests at `stories/lego-void`; delete `data/`; drop `Entity.reference_image`; set `Config.min_pause_seconds` to the shipped 15; fix "first segment" and "twice" in the docs; let the runner read the engine's `clip_seconds` (§7, §12).

### Design changes requiring approval

1. Landing guard and decay for viewer momentum (§5.4), after E1.
2. One owner per stagnation rung: Showrunner replan at 2× or one beat after the Director's "REQUIRED" (§5.5).
3. Candidate beats hidden while a viewer direction is active (§5.7), after E5.
4. Async bootstrap with a blueprint-derived opening card (§2.4), after E8.
5. Fail-soft policy for `emit()` exceptions and world rollback on a failed render (§5.8), with Prompt 03.
6. Thread expiry by age (§4.7).

### Hypotheses requiring experiments

E1 momentum; E2 Showrunner memory-only; E3 beat cap in seconds; E4 recent clips; E5 candidates during excursions; E6 retry frequency; E7 presence decay; E8 async bootstrap; E9 anchor length; E10 voice-over floor (§11).

### Items that should remain unchanged

The two-role, two-clock split. The beat as the Director's unit and the per-clip ops. The typed world model with character state and the single-writer rule. The validator's violation-versus-warning split and its one retry. The system prompt's stability and its size. Strict structured outputs. The compiler as the only speaker of the video model's dialect, with the anchor on continuing clips. The just-in-time loop and interrupt-on-input. Pacing in story seconds. The scene-length cap. The intervention ledger and the no-disposition correction. The context caps.
