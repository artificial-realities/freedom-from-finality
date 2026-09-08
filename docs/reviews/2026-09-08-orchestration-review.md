# Architectural review — Artificial Realities orchestration layer

Date: 2026-09-08. Scope: the LLM orchestration, state, memory, and the narrative→video boundary. Reviewed against the code as of commit 50c9d8c, four transcript logs (including the 113-turn live LTX-2.5 run of 2026-09-07), the pre-cleanup findings recovered from git history (benchmark, memory A/B, streaming spike), and the current literature.

Verdict in one line: the system is over-engineered on the per-turn LLM roster (four calls per clip, two of which do not earn their keep) and under-built on the deterministic layer that the vision actually depends on (structured world state, a plan, an event log, pacing control, and a real boundary to the video model).

---

## 1. What the system actually does today (verified from code)

### 1.1 The four roles

| Role | Call | Reads | Writes | Consumed by |
|---|---|---|---|---|
| **Tolkien** (beat writer) | strict JSON `Beat` | rules, protagonist, premise; long/short narrative; `world_state` JSON; memory block (rolling prose, or Mem0 top-6); last 3 narrations; `context_brief`; `user_input` | `narration`, `action`, `outcome`, `short_term_narrative`, `long_term_narrative?` | Spielberg, Attenborough, Spock, log, Mem0 |
| **Spielberg** (shot composer) | strict JSON `Shot` | `visual_style`; all locations and characters; beat; previous `end_frame_description`; location, inventory, rest of `world_state` | `i2v_prompt`, `on_screen`, `camera`, `motion`, `end_frame_description`, `duration_seconds` | **Only `i2v_prompt` and `duration_seconds` reach the renderer.** `camera`/`motion` go to Attenborough's prompt; `end_frame_description` goes to next turn's Spielberg; `on_screen` goes to Spock |
| **Attenborough** (voice-over) | strict JSON `Commentary` | `tone_guidelines`; beats since last line; beat; shot camera/motion/end-frame; clip duration; `short_term_narrative`; memory block; last 5 lines; silence label | `voiceover` (+ TTS side effect) | ElevenLabs when `audio_enabled`; otherwise the log only |
| **Spock** (editor, async) | non-strict JSON `MemoryUpdate` | locations, characters; beat; shot (prompt, on_screen, end frame); voiceover; `world_state`; `narrative_memory`; last 5 full history entries | `world_state_delta` (merged), `narrative_memory` (full rewrite), `context_brief` (2 lines) | Tolkien (next turn), Spielberg (location/inventory), log |

Turn order (`src/graph/mas_graph.py:61-87`): join last turn's Spock → Tolkien → Spielberg → narrator gate → Attenborough (if open) → tick story clock → spawn Spock on a deep snapshot. The runner then renders (pipelined against the next turn's LLM calls in live mode), commits a `HistoryEntry`, and appends to the markdown log.

### 1.2 Where state lives

- `StoryState` is a single in-memory Pydantic object. **Nothing is persisted to disk except the markdown transcript** (and the Mem0 store in mem0 mode). A session cannot be resumed.
- The blueprint (`data/story.json`) is static: title, synopsis, visual style, tone, locations `{name, description}`, characters `{name, description}`, rules, premise, seed directions.
- `world_state` is a free-form `dict`. By convention it holds `protagonist_location: str`, `inventory: list[str]` (meaning *held or worn*), and `characters: {name: {free dict}}`. In practice the character dict only ever holds `{'holding': ...}`.
- `narrative_memory` is prose that Spock rewrites in full every turn. `context_brief` is two lines of prose.
- `history` is a growing list of `(turn, user_input, beat, shot, commentary)`; only the last 3–5 entries are ever read.
- The video model's sliding window (the last 9 frames of the previous chunk, about 0.4 s) is the **only visual memory in the entire system**. No frame ever comes back to the LLM layer.

### 1.3 How the story is remembered

Four overlapping episodic representations exist: the verbatim last 3 narrations (Tolkien), the rolling prose blob (Spock, rewritten every turn), the two-line brief (Spock), and, optionally, Mem0 extractions with vector retrieval. Plus the runner's full `history` list, which no prompt reads beyond a 5-entry window.

### 1.4 How user input propagates

The live loop drains the input queue and keeps only the **most recent** non-empty line (`src/eval/runner.py:703-708`); if the viewer types two things between clips, the first is silently dropped. The surviving line goes into `state.user_input`, which **only Tolkien reads**. Tolkien is told to honor it or bend toward it. There is no classification of the input, no check against the world rules other than Tolkien's own judgment, and no record of the intervention beyond the `HistoryEntry`. Since Tolkien's prompt shows past turns as narration only, **a steer from six clips ago is invisible to the writer** unless Spock happened to fold it into the prose memory.

### 1.5 How video is coordinated

Spielberg writes a 100–190-word prose prompt. The runner posts it with a frame count to the resident server (`src/i2v/ltxv.py`), which conditions on the previous chunk's tail at `continuity_strength` and on the reference image only on turn 1 (`server/ltx2_server.py:296-307`). That is the whole boundary: one paragraph of text plus a duration. Continuity at the LLM layer is carried by `end_frame_description`, which is **Spielberg's intention, not an observation**. The next shot is written as "continuing from" a frame nobody has seen. When the model drifts (brown torso, LEGO baseplate background, both observed in the spike), the agent layer keeps writing as if it had not.

### 1.6 Duplication, overlap, loss, and waste (with evidence)

**Duplication.** `Beat.narration` is re-read by three LLM calls and re-written by two (into `i2v_prompt` and into `narrative_memory`), then extracted a third time by Mem0 in that mode. Spielberg re-emits the visual-style paragraph and the full character descriptor on every turn; both are static text a template could insert. Spock's `Honoring:` line and Tolkien's `short_term_narrative` are both "what to do next"; in the live log they restate each other on nearly every turn.

**Overlap.** Tolkien and Spock both maintain forward direction. Spielberg (`on_screen`) and Spock (`inventory`, `characters`) both track what is present. Tolkien's `outcome` is one LLM writing a hint for another LLM to turn into structure.

**Information loss.**
- Props that are in the scene but not held are tracked nowhere. In the scripted run of 2026-06-12 the soccer ball, skateboard, harmonica and duck pedestal were all in play at turn 23 while `inventory` read `['bright red cap']`. In the live run `inventory` was `['']` at turn 20 and empty at turn 50 while the apple was the entire plot. The delta semantics (`list` replaces) let an empty string through unvalidated (`src/agents/spock.py:135-153`).
- The rolling prose rewrite is lossy by construction; plan 02 had already identified "overwriting earlier-correct details from turn ~50" as its failure mode.
- Past user interventions are not represented anywhere the writer reads.
- Multiple inputs between clips collapse to one.
- What was rendered never feeds back.
- In mem0 mode the retrieval query is `user_input + short_term_narrative` (`src/agents/tolkien.py:48-53`), so retrieval is biased toward the current direction and will rarely surface a dormant prop for the callbacks the premise is built on.

**Unnecessary LLM calls.**
- All three shipped configs have `audio_enabled: false`. In the 113-turn live run Attenborough was invoked on every turn and spoke on 110 of them; every one of those lines was discarded. That is roughly a quarter of the per-turn LLM spend producing nothing. The prompt asks for silence "more often than you think"; the model chose silence 3 times in 113.
- The narrator gate never closes in live mode: the pipelined path adds the expected clip duration to `silence_seconds` on every turn and nothing resets it (`src/eval/runner.py:753`), so `silence ≥ min_pause` is always true. The "narrator clock" is a no-op in the configuration that matters.
- Spock's full prose rewrite costs ~300 output words per turn; the Mem0 alternative replaced it with ~9K-token extraction calls and raised session cost 31% for parity at 30 turns (plan 02 §7).
- Spielberg spends most of its output re-rendering narration into a template plus boilerplate. Across the live run `camera` was "medium shot, straight-on" on nearly every turn and `duration_seconds` was 7 on nearly every turn despite the "default 5" instruction.

**Small bugs found on the way.** Attenborough is told the clip duration is Spielberg's choice but receives `config.i2v_duration` (`src/agents/attenborough.py:89`). `Config` still carries `i2v_duration` as the narrator's clock unit while the renderer uses the shot's duration. `_coerce_state` and the `build_graph` dispatch are LangGraph vestiges.

### 1.7 The behavior that matters most

The live run is the clearest evidence about the architecture. With a silent viewer, the story stayed on **one apple for all 113 turns, about 13 minutes of film**. A butterfly net appeared around turn 55; nothing else ever did. The blueprint's long-term direction says bits should escalate and props should accumulate. There is no component whose job is to notice that the story has not progressed, and the only forward-looking state is one sentence of `short_term_narrative`, which is a greedy local continuation. This is exactly the failure NARRA-Gym names: "the story sounds good turn-by-turn, yet never actually progresses."

---

## 2. Is the multi-agent approach over-engineered? Per-role verdicts

The school benchmark (N=3, 180 turns, recovered from git) is the only in-house evidence and should be read precisely: decomposition beat a single call on *late-game prop persistence and long-horizon coherence* (+0.3 on a 0–3 scale), tied on local quality, and *lost* on late-game internal coherence because agents writing in isolation drift from each other. The stated mechanism for the win was that Spock did memory compression so the writer did not have to do it in the same response. That is an argument for **separating memory work from writing**, not for four LLM roles.

**Tolkien.** Deciding what happens next is genuinely creative and needs an LLM. Keep, as the core Director. What it lacks is a plan to advance toward, structured effects to emit, and memory of what the viewer asked for.

**Spielberg.** Today it is about 80% template: style boilerplate + character descriptor + narration paraphrase + a near-constant camera line. The literature does support keeping "how it's shot" separable from "what happens" (FilmAgent's ablation found camera the weakest single-agent output; Camera Artist found the cinematic-language step can be a cheap rewriter rather than a full agent). The right resolution is to keep the *boundary as a schema section* and stop paying a *second serial call* for it: the Director emits a structured shot spec, and deterministic code compiles the LTX prompt from that spec plus the style bible and character sheets. This is also the latency-critical change: once the substrate approaches real time, the per-segment LLM budget must fit inside one clip duration, and two serial ~4 s calls do not.

**Attenborough.** Remove as an agent. Its only responsibility is narration, it is disabled in every shipped config, its gate is broken in live mode, and LTX-2.5 renders speech and narration natively from prompt text. Narration stays as a *capability*: an optional `voiceover` field in the Director's output, gated deterministically, rendered either through the LTX prompt or through TTS when a stable narrator voice is required. The one real finding against this (tone guidelines bleeding into the writer's prose) is mitigated by structured fields and by making narration rare by policy rather than by a separate clock; it should be checked in the A/B, not assumed.

**Spock.** Split its three jobs. The world-state delta should be emitted by the Director in the same call (it just decided what happened and knows the delta best) and **validated and applied by code**. The rolling prose rewrite should be replaced by an append-only event log plus periodic compaction on a slow clock. The two-line brief becomes redundant once open threads and active entities are structured state the Director reads directly. What survives is a slow-clock reflection and planning call, not a per-turn agent.

**Does any agent hold information another does not?** Only by design choice, and the choices are mostly wrong: Spielberg cannot see the user's request or the memory; Tolkien cannot see what is on screen; nobody can see what was rendered. The only justified slice is keeping the video-prompt dialect out of the writer's prose, and a compiler achieves that better than a second agent.

**Is anything mainly transforming another LLM's output without adding reasoning?** Yes: Spielberg on most turns, Spock's `narrative_memory` and `context_brief` on most turns, and Mem0's extraction step in that mode.

---

## 3. What the state of the art says (and what transfers)

- **When multi-agent pays.** Anthropic's research system: multi-agent wins on breadth-first, parallelizable work and costs ~15× the tokens of chat; it underperforms on sequential, shared-context work. Cognition: split context is the dominant failure; "one main loop carries state, workers are stateless and narrow." MAST (NeurIPS 2025): most failures are system-design and inter-agent misalignment, not model capability. A story that advances one beat at a time is the sequential, shared-context case. → One writer per segment, deterministic state, narrow off-path helpers.
- **Context engineering.** Anthropic's guidance: compaction, structured note-taking outside the window, and sub-agents only for isolated deep dives. The current rolling-rewrite is compaction done every turn with no source of truth to compact from. → Keep an event log; compact on a slow clock; put structured notes (state, threads) in the prompt, not prose retellings.
- **Memory for narrative specifically.** Generative Agents: an append-only memory stream, retrieval by recency×importance×relevance, and periodic *reflection* into higher-level memories. Zep/Graphiti and Mem0 are conversational-memory products; on LongMemEval Zep's temporal graph beats Mem0 by ~15 points, and the Narrative World Model paper (2026) shows a *narratology-typed* state (who knows what and when, setups vs payoffs, relationship shifts) beats both Graphiti and flat retrieval on multi-hop story questions, with the gain traced to the representation, not the extractor. The project's own Mem0 A/B found parity at +31% cost. → Structured narrative state first; generic memory frameworks are not the right primitive here; retrieval is an add-on for very long runs.
- **Interactive narrative.** StoryVerse: author-level "abstract acts" instantiated by a planner against world state and player action, with replanning on divergence. Dramamancer (UIST 2025): author schemas → storylets with preconditions and outcomes; an LLM instantiates lines within them. Orchestrated Reality (2026): a singleton orchestrator owns canonical JSON world state; character LLMs see only a projection; changes go through Plan → Diff → Validate → Apply. NARRA-Gym (2026): stagnation detection plus an escalation ladder that makes structural advancement mandatory as a scene drags; memory stratified by update frequency. Left 4 Dead's Director: an intensity curve with build-up/peak/relax phases and "structured unpredictability." → A soft hierarchical plan (arc → scene → beat), deterministic validation of LLM-proposed deltas, a stagnation trigger, and an intensity curve.
- **Rule adherence.** "Seduced by the Narrative" (2026): neither model scale nor reasoning reliably keeps an LLM adjudicator on the rules under narrative pressure. The school benchmark saw the same (rule compliance ~1.1/3 for *both* configs). → Rules that can be checked structurally (cast registry, no new characters, props must exist) must be checked by code.
- **Video systems.** MovieAgent/FilmAgent/Camera Artist are offline batch pipelines; their transferable pieces are the typed shot schema and the character bank. GroundShot (2026) manages consistency with an *entity-level visual memory* built from accepted shots and verified before reuse; IAMFlow puts the LLM in the per-chunk loop only for memory curation with async VLM verification. ShotStream and CausalCine (2026) make "next-shot generation conditioned on history" real-time with explicit shot transitions and bounded caches; LongLive is real-time with prompt switching. LTX-2.5 itself supports keyframe conditioning at interior positions, multishot prompting (≤3 shots per generation), speech in prompts, and is documented to want prompts structured as sound → camera → emotion → shot type → scene → action with characters re-identified at each cut. → The boundary should be a typed segment spec compiled into that dialect; cuts are a first-class transition; an entity-level visual memory with verification is the identity-drift path.
- **Nobody has published an LLM director loop driving a real-time steerable video model.** Plan 03's finding still holds. That is the project's ground, and it argues for keeping the per-segment LLM output small and structured.

---

## 4. Memory: what should exist, how long it lives, and what goes in the prompt

| Tier | Representation | Lifetime | Owner | In the prompt? |
|---|---|---|---|---|
| Story bible | rules, premise, style, tone, cast sheets (static), locations | immutable | authored | Always, as a static prefix (cache-friendly) |
| World state | typed entities: characters, props, locations with `status`, `location`, `holder`, `last_seen`; relationships (later); character knowledge (later) | current truth, mutated only by validated deltas | code | Active entities in full; dormant by name only |
| Scene context | scene card (goal, tension, exit condition, intensity target), last K beats verbatim, last shot spec | one scene | Director/Showrunner | Always |
| Open threads | list of setups, promises, running gags with `status` and `opened_at` | until paid off or pruned | Director opens/closes; Showrunner prunes | Always, bounded (≤8) |
| Interventions | every user input with segment index and disposition (absorbed now / deferred / declined + reason) | whole run | code + Director | Recent and pending ones |
| Event log | one record per segment: beat, delta, user input, shot spec | append-only, persisted | code | Only the recent K |
| Summaries | scene summaries → act summaries, produced by compaction | derived, regenerable | Showrunner | Act summaries + current scene summary |
| Retrieval | semantic index over log + summaries | later | service | Top-k when the run outgrows summaries |

How this answers the review's list: important events → event log + summaries; relationships and character knowledge → world state fields (deferred until there is more than one character); promises → open threads; locations/objects/injuries → entities with status; unresolved plot threads → open threads; user interventions → interventions log; story rules and personalities → bible; long-term goals → arc in the plan; recent scene context → scene context tier; current situation → world state + last beat.

Conflicting or stale memory: world state is authoritative for now, the event log for what happened, summaries are derived and can be rebuilt from the log. Deltas that reference unknown entities or impossible transitions are rejected and the Director is re-asked once with the violation. `last_seen` makes dormancy explicit, which is what a callback needs.

---

## 5. Plan versus influence

There should be a plan, and it should be soft. Three levels: an **arc** (a handful of goals or acts, replannable), a **scene card** (goal, tension, exit condition, intensity target, and a short list of candidate next beats with preconditions, in the storylet sense), and the **beat** the Director writes each segment. User input is absorbed at the lowest level that fits: most inputs are beat-level and land immediately; some change the scene goal; a few require re-planning the arc. The Director makes that classification in its per-segment call and records it as the intervention's disposition, so the viewer's request is remembered even when it is deferred. Conflicts with fundamental constraints are decided in two layers: code rejects what it can check structurally (a new character in a one-character world, a prop that does not exist), and the Director handles the rest with the rules in its prompt, steering around rather than refusing.

Replanning is event-driven rather than every turn: scene exit condition met, an arc-level intervention, a stagnation trigger (N segments with no state change and no new entity, or the same scene goal for too long), or a hard cap. Causality is preserved because every beat is written against validated state and logged, and the Showrunner compacts from the log, not from its own previous summary.

---

## 6. The narrator

Confirmed: Attenborough is a dedicated narrator agent whose only responsibility is narration. It also carries pacing logic (speak or stay silent) that the engine already half-owns via the gate. Recommendation: remove the agent; keep narration as an optional field the Director fills when the gate is open; keep the gate deterministic and fix it; render narration through LTX native audio or TTS. Its non-narration function (choosing when silence is right) moves to policy plus the Director.

---

## 7. The video boundary

The narrative layer should hand the renderer a typed **segment spec**, not a paragraph:

- `shot`: subject and action beats in order; camera (framing, lens feel, movement); lighting/style reference (from the bible); `on_screen` entities by id.
- `audio`: ambient cues attached to visible sources; optional `dialogue` lines with speaker id; optional `voiceover`.
- `continuity`: `transition: continue | cut`; which entities must match the previous segment; references to inject (character reference images by id) and strength.
- `timing`: duration; whether this segment is one shot or up to three.

A deterministic compiler turns that into the prompt dialect the current model wants (for LTX-2.5: sound first, camera, emotion, shot type, scene, action; characters re-identified verbatim from their sheet at every cut; dialogue in quotes). When the model changes, the compiler changes; the schema does not. **Cuts matter for identity**: today every clip continues from the previous tail, so drift compounds forever. A `cut` re-seeds from the reference (or a verified keyframe), which bounds drift and is also a normal cinematic device. The server needs one small addition for this (a "reset window to reference" flag). Keyframe conditioning, per-character reference adapters, and a VLM observation loop that updates an entity-level visual memory are the right next steps but should wait.

---

## 8. Overall assessment

- **Over-engineered:** four LLM calls per clip where one is needed on the critical path; a narrator agent and clock that produce nothing in live mode; a per-turn memory-rewrite agent; three overlapping episodic memories; the span/mux machinery built around an audio path that is off.
- **Missing:** a plan layer; typed entity state for props and locations; an event log and persistence; intervention memory; stagnation and pacing control; a compiler at the video boundary; cut/continue semantics; any observation of what was rendered.
- **Conceptually flawed:** memory as an LLM-rewritten prose blob; continuity as a text description of an unseen frame; "one turn = one beat = one clip"; role-disciplined slicing that hides the user's request from the shot composer and the screen contents from the writer.
- **Right and worth keeping:** workflow-not-agent control flow, Pydantic schemas with native structured outputs, the async off-path principle for heavy work, the "what happens / how it's shot" boundary (as schema sections), the story bible, prompt templates as files, render pipelining, the resident server owning the sliding window, fail-soft services, Langfuse tracing, and the convention that content is produced whether or not it is rendered.

---

## 9. Proposed architecture

```
                    ┌─────────────────────────────────────────────┐
  viewer input ───▶ │ Engine (deterministic, asyncio)              │
                    │  • intervention queue (all inputs kept)      │
                    │  • pacing: VO gate, intensity, stagnation    │
                    │  • pipelining, persistence per segment       │
                    └───────┬───────────────────────────┬──────────┘
                            │ assemble context           │ triggers (scene exit,
                            ▼                            │  stagnation, arc input,
              ┌──────────────────────────┐              │  every N segments)
              │ Context assembler (code) │              ▼
              │ bible · scene card ·     │   ┌────────────────────────────┐
              │ active entities · threads│   │ SHOWRUNNER (LLM, slow clock)│
              │ · last K beats · pending │   │ compaction of event log →   │
              │ interventions · gates    │   │ summaries; update cast sheets│
              └──────────┬───────────────┘   │ prune threads; re-plan arc /│
                         ▼                   │ next scene card + candidates│
              ┌──────────────────────────┐   └──────────┬─────────────────┘
              │ DIRECTOR (LLM, per seg.) │              │ lands before next
              │ one structured call:     │◀─────────────┘ Director read
              │ beat · shot spec · audio │
              │ · state delta · thread   │
              │ ops · intervention       │
              │ disposition · flags      │
              └──────────┬───────────────┘
                         ▼
              ┌──────────────────────────┐   reject + re-ask once
              │ Validator (code)         │──────────────────────▶ Director
              │ schema · cast registry · │
              │ entity existence · rules │
              └──────────┬───────────────┘
                         ▼
   ┌───────────────────────────────────────────────┐
   │ State store (typed, persisted)                 │
   │ world state · open threads · interventions ·   │
   │ event log · summaries · plan                   │
   └──────────┬────────────────────────────────────┘
              ▼
   ┌──────────────────────────┐    ┌───────────────┐    ┌──────────────┐
   │ Prompt compiler (code)   │───▶│ LTX-2.5 server│───▶│ player/mux   │
   │ spec → model dialect;    │    │ continue/cut, │    │              │
   │ continuity plan          │    │ refs, duration│    └──────────────┘
   └──────────────────────────┘    └───────────────┘
              │ optional, later: TTS for a fixed narrator voice;
              ▼ VLM observation → entity visual memory → compiler
```

Two LLM roles on two clocks. The Director runs once per segment on the critical path and is the only LLM the segment waits for. The Showrunner runs off-path when triggered and combines reflection (compaction, cast-sheet updates, thread pruning) with planning (arc, next scene card, candidate beats). Everything else is code.

Information flow: engine assembles context → Director proposes → validator checks → state applies → compiler renders → server generates → log persists. The Showrunner reads the log and state and writes summaries and the plan; the one-turn-delay principle survives as "Showrunner output lands before the next Director read, or is skipped."

---

## 10. Current → recommended, with priorities

| # | Change | Why / problem solved | Tradeoff | Priority |
|---|---|---|---|---|
| 1 | Remove Attenborough as an agent; `voiceover` becomes an optional Director field behind a deterministic gate; render via LTX audio prompt or TTS | 110 wasted calls in 113 turns; gate is broken in live; LTX renders speech; user does not want a narrator agent | Tone bleed into the writer's prose is possible; mitigated by structured fields and rarity; verify in A/B | **Critical** |
| 2 | Replace Spock's per-turn call: Director emits the state delta; code validates and applies; append-only event log; Showrunner compacts on a slow clock | Lossy prose rewrite, untracked props, unvalidated deltas, cost | Director output grows by one section; compaction is periodic so summaries lag by up to N segments (the log does not) | **Critical** |
| 3 | Typed world model (entities with status/holder/location/last_seen, threads, interventions, plan) + validator + on-disk persistence per segment | Props on the floor are untracked; `['']` inventories; no resume; rules unenforced | A schema to maintain; migration of `story.json` cast into entity sheets | **Critical** |
| 4 | Plan layer: arc → scene card with candidate beats → beat; stagnation trigger and intensity curve; event-driven replanning by the Showrunner | 113 turns of one apple; no escalation; no way to recover from divergence | One more prompt to author; risk of over-planning if triggers fire too often; keep the plan soft | **Critical** |
| 5 | Fold Spielberg into the Director as a structured shot section; deterministic LTX prompt compiler | Second serial call is mostly template; critical path must fit one clip when the substrate speeds up; style/character boilerplate belongs in code | Loses the "second opinion" on camera; A/B on the 30-turn scenario, and keep a cheap rewriter as the fallback | **Strong** |
| 6 | `transition: continue \| cut` in the spec; server flag to re-seed from the reference on a cut | Drift compounds because nothing ever cuts; cuts are cinematic and free identity resets | Cuts change the feel; policy decides frequency | **Strong** |
| 7 | Intervention handling: keep every input, Director records a disposition, pending ones stay visible | Inputs dropped; steers forgotten; no basis for "the viewer feels in control" | Small prompt cost | **Strong** |
| 8 | Drop Mem0 for now; keep the retrieval interface for later | Parity at +31% cost; wrong primitive for narrative state | Long-run recall relies on summaries until retrieval returns | **Nice** |
| 9 | Decouple segment from fixed clip: allow 1–3 shots and variable duration per segment | LTX-2.5 supports multishot; "one turn = one clip" is an artifact | Pacing math becomes per-segment | **Nice** |
| 10 | Fix small bugs regardless (narrator gate, `clip_duration` passthrough, inventory validation, LangGraph vestiges) | Correctness | none | **Nice** (subsumed by 1–3) |
| — | Keep: deterministic engine, Pydantic + structured outputs, async off-path pattern, bible, prompt files, pipelining, resident server, fail-soft services, tracing, content-not-opt-out convention | Validated or sound | — | **Keep as-is** |

Expected effect on the per-segment path: 4 LLM calls → 1 on the critical path plus a periodic Showrunner call; roughly 50–60% lower LLM cost per segment; critical-path latency halved; the same or better long-horizon consistency because the memory work that made decomposition win is now done by code and a slow clock rather than by a fourth agent.

---

## 11. Build now vs design for later

**Now.** Items 1–4 and 7 above, plus the minimal version of 5 and 6 (schema fields and the compiler; the server cut flag). Concretely: `Segment` schema, `WorldModel` with entity registry and validator, event log with persistence, `Showrunner` prompt with scene card + compaction, prompt compiler, engine changes for gates/triggers, and a 30-turn and a 150-turn scripted A/B against the current system using the existing scenario. Keep the old four-role engine behind the `graph:` key until the A/B is in.

**Later.** VLM observation of rendered frames feeding an entity visual memory (the real identity-drift fix at the agent layer); per-character reference images and keyframe conditioning; semantic retrieval over the event log; character knowledge and relationship graphs (irrelevant until there is more than one character); a separate cinematic-language rewriter if the compiler's prose is not good enough; multi-viewer input aggregation; swapping the substrate for a real-time next-shot model (ShotStream/LongLive class) when one is self-hostable with the fidelity needed; a proper evaluation harness beyond deterministic log metrics.

**Do not build.** A per-turn critic/verifier agent (a validator does the checkable part; the rest showed no benefit in the benchmark); a POMDP-style formal drama manager; a general-purpose memory framework as the system of record; more agents.

---

## 12. Sources

Multi-agent and context: MAST, arXiv 2503.13657; Cognition, "Don't Build Multi-Agents"; Anthropic, "Effective context engineering for AI agents" and "How we built our multi-agent research system". Memory: Park et al., Generative Agents (2023); Zep, arXiv 2501.13956; Mem0, arXiv 2504.19413; Narrative World Model, arXiv 2607.05577. Interactive narrative: StoryVerse, arXiv 2405.13042; Dramamancer, arXiv 2601.18785; Orchestrated Reality, arXiv 2606.16014; NARRA-Gym, arXiv 2605.08503; "Seduced by the Narrative", arXiv 2607.02802; Valve's Left 4 Dead AI Director. Video: MovieAgent, arXiv 2503.07314; FilmAgent, arXiv 2501.12909; Camera Artist, arXiv 2604.09195; GroundShot, arXiv 2606.20799; IAMFlow, arXiv 2605.18733; StreamChar, arXiv 2605.25659; ShotStream, arXiv 2603.25746; CausalCine, arXiv 2605.12496; LongLive, arXiv 2509.22622; Google DeepMind Genie 3; LTX-2 conditioning docs and the LTX-2.5 prompt guide; Fable's SHOW-1/Showrunner. In-repo evidence: `docs/findings/benchmark-results.md`, `plans/02-memory-swap.md`, `plans/03-agent-redesign.md`, `plans/04-streaming-spike.md` (all at commit `db7c1cb^`), and the transcripts under `logs/`.
