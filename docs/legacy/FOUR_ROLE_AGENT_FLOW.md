> **Legacy.** This document describes the four-role engine (`graph: "mas_graph"`), kept for A/B comparison. The current design is the Director engine in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md); the reasoning behind the change is in [`docs/reviews/2026-09-08-orchestration-review.md`](../reviews/2026-09-08-orchestration-review.md).

# Agent Flow

A focused walkthrough of the turn engine: which agent runs when, what each
agent reads from `StoryState`, and what it writes back. For the wider design
rationale, see `ARCHITECTURE.md`; this doc is the short, hands-on reference.

The single source of truth for shape is `src/state/story_state.py` and
`src/models/responses.py`. The orchestration is the `TurnEngine` in
`src/graph/mas_graph.py`.

---

## 1. The turn at a glance

Since plan 03 the roles run on their own clocks, not as one serial chain:

```
join: apply last turn's editor result (world_state, context_brief[, narrative_memory])
  │
  ▼                    critical path (every turn)
                     ┌──────────────┐
   user_input ─────▶ │   Tolkien    │  beat writer  ────────▶ current_beat
                     └──────┬───────┘                          long_term_narrative
                            │                                  short_term_narrative
                            ▼
                     ┌──────────────┐
                     │  Spielberg   │  shot composer ────────▶ current_shot
                     └──────┬───────┘
                            │
        ┌───────────────────┴────────────────────┐
        ▼ narrator clock (silence floor open?)    ▼ editor (async task)
  ┌──────────────┐                          ┌──────────────┐
  │ Attenborough │ ──▶ current_commentary   │    Spock     │ ──▶ world_state
  └──────────────┘     current_audio_path   └──────────────┘     context_brief
   (gate closed: engine writes               (+ narrative_memory in prose mode,
    a silent Commentary, no LLM call)         + Mem0 write in mem0 mode;
                                              joined at the start of turn N+1)
        │
        ▼
  end of turn ─── runner appends HistoryEntry, render starts
```

No retries, no branches on the critical path. Drift is caught the *next* turn
via Spock's `context_brief`, applied at the join before Tolkien reads.

---

## 2. State, by ownership

`StoryState` is a Pydantic model. Fields group by who owns them:

| Group | Fields | Owner / lifetime |
|---|---|---|
| **Blueprint** (read-only after init) | `title`, `synopsis`, `visual_style`, `tone_guidelines`, `locations`, `characters`, `world_constraints`, `narrative_premise` | Loaded once from `data/story.json` |
| **Turn input** | `turn_number`, `user_input` | Set by runner before each turn |
| **Per-turn outputs** | `current_beat`, `current_shot`, `current_commentary`, `current_audio_path` | Written by their respective agent that turn; cleared between turns conceptually (overwritten) |
| **Narrative direction** | `long_term_narrative`, `short_term_narrative` | Maintained by Tolkien |
| **Persistent memory** | `world_state`, `narrative_memory`, `context_brief` | Maintained by Spock |
| **Narrator-clock pacing** | `audio_seconds_owed`, `silence_seconds`, `last_clip_duration`, `pacing_managed` | Measured by the live producer when audio plays (`pacing_managed`); otherwise ticked by the engine as story-time |
| **History** | `history: list[HistoryEntry]` | Appended by the runner at end of turn, once beat+shot+commentary all exist |
| **Meta** | `config_name` | Set at init |

`world_state` is a free-form dict but conventionally tracks
`protagonist_location`, `inventory`, and `characters` (a name → dict map).
`apply_world_delta` enforces the partial-update semantics.

---

## 3. Agent-by-agent

For each agent: what it reads, what it writes, and the one-line purpose.
"Reads" is what the prompt actually sees, derived from `src/agents/*.py`.

### 3.1 Tolkien — beat writer
**Purpose.** Decides what *happens* this turn: a beat with `narration`,
`action`, `outcome`, plus updated narrative direction.

**Reads from state**
- Blueprint: `world_constraints`, first character (protagonist),
  `narrative_premise`
- Direction: `long_term_narrative`, `short_term_narrative`
- Memory: `world_state`, `narrative_memory`
- Feedback loop: `context_brief` *(written by Spock on the previous turn)*
- Recent: last 3 beats' `narration`
- Input: `turn_number`, `user_input`

**Writes**
- `current_beat: Beat`
- `short_term_narrative` (always refreshed if the model returned one)
- `long_term_narrative` (only if the model returned a non-empty value)

**Notes.** On turn 1 the brief, memory, and history are empty; Tolkien
opens from blueprint material alone.

---

### 3.2 Spielberg — shot composer
**Purpose.** Translate the beat into a concrete i2v prompt and frame anchors.

**Reads from state**
- Blueprint: `visual_style`, full `locations`, full `characters`
- Beat just written: `current_beat.narration / .action / .outcome`
- Continuity: `previous_end_frame_description` (last item in `history`)
- World snapshot: `protagonist_location`, `inventory`, and the rest of
  `world_state`

**Writes**
- `current_shot: Shot` (`i2v_prompt`, `on_screen`, `camera`, `motion`,
  `end_frame_description`, `duration_seconds`)

**Skip condition.** If `current_beat is None`, returns `{}` and the turn
proceeds with no shot — downstream agents will then bail out too.

---

### 3.3 Attenborough — voiceover commentator (narrator clock)
**Purpose.** When the room opens, decide whether to speak and what to say.

**Invocation.** The turn engine — not the agent — holds the pacing gate:
Attenborough is only called when `audio_seconds_owed ≈ 0` and
`silence_seconds ≥ config.min_pause_seconds` (or no line has been spoken
yet). On held turns the engine writes a silent `Commentary` without an
LLM call. The clock ticks in every mode: real measured playback time when
the live producer manages pacing, story-time (one turn ≈ one clip of
`i2v_duration`) otherwise.

**Reads from state**
- Blueprint: `tone_guidelines`
- Accumulated: narrations since his last non-empty line (capped)
- Beat: `current_beat.narration / .action / .outcome`
- Direction: `short_term_narrative`
- Shot: `camera`, `motion`, `end_frame_description`, `duration_seconds`
- Memory: rolling prose or Mem0 retrieval (`memory_block`)
- Recent: last *N* commentary lines (`config.context_window_history`)
- Pacing: a humanized `silence_label` derived from `silence_seconds`

**Writes**
- `current_commentary: Commentary` (empty `voiceover` = stay silent —
  still a first-class choice even when invoked)
- `current_audio_path` (set when `audio_enabled` and TTS succeeds)

**TTS side effect.** When `audio_enabled` and a `tts` backend is wired,
the runtime synthesizes `voiceover` to a file and stores its path on
`current_audio_path`. Failures never block the turn.

---

### 3.4 Spock — memory & context curator (async editor)
**Purpose.** Apply this turn's effects to memory and produce the
`context_brief` that lands in Tolkien's prompt next turn.

**Invocation.** The engine spawns Spock as an asyncio task on a deep
snapshot of the finished turn and joins the task at the start of the
next turn — his work overlaps rendering and user wait. A failed task is
logged and skipped (state carries forward unchanged). `engine.drain()`
joins the final task at session end.

**Modes (`config.memory_backend`).** In `prose` mode (legacy) Spock also
rewrites the rolling `narrative_memory` each turn. In `mem0` mode
(`configs/mas.mem0.yaml`) he emits only the delta + brief; episodic
memory is written to an embedded Mem0 store
(`src/memory/mem0_store.py`) inside the same editor task, and Tolkien /
Attenborough each retrieve relevant memories per turn (`search_block`)
in place of the prose blob.

**Reads from state**
- Blueprint: `locations`, `characters`
- This turn's outputs: full `current_beat`, `current_shot.i2v_prompt`,
  `current_shot.on_screen`, `current_shot.end_frame_description`,
  `current_commentary.voiceover`
- Memory: `world_state`, `narrative_memory`
- Recent: last *N* full history entries (`config.context_window_history`)

**Writes**
- `world_state` ← merged via `WorldStateDelta` (partial semantics:
  `protagonist_location` empty = unchanged; `inventory` `None` = unchanged
  vs list = full replace; `characters` per-name partial merge)
- `narrative_memory` (rewritten in full; target token budget from config)
- `context_brief` — narrow prompt-payload for Tolkien's *next* turn.
  Pinned two-line format:
  ```
  In scene: <Name — one-line summary, ...>     | (none)
  Honoring: <setup; setup; ...>                 | (none)
  ```
  Both lines always present. The whole brief collapses to `""` only when
  both would be `(none)`. The format is enforced in `spock.system.md`;
  earlier prose drift was the trigger for committing.

**Skip condition.** If beat / shot / commentary are missing, returns `{}`
and the previous memory carries forward unchanged.

---

## 4. State evolution across turns

The interesting part is the **one-turn-delayed feedback loop**: Spock's
`context_brief` written at the end of turn N is the first thing Tolkien
reads at the start of turn N+1.

```
Turn N                                            Turn N+1
─────────────────────────                         ─────────────────────────
state.user_input = U_N                            state.user_input = U_{N+1}
state.context_brief = B_{N-1}                     state.context_brief = B_N   ◀── written by Spock at end of N
state.world_state = W_{N-1}                       state.world_state = W_N
state.narrative_memory = M_{N-1}                  state.narrative_memory = M_N
state.history = [..., entry_{N-1}]                state.history = [..., entry_N]

   ┌──────────────────────────────────────┐
   │  Tolkien                              │
   │    reads context_brief = B_{N-1}      │     ┌────────────────────────────┐
   │    writes current_beat (beat_N),      │     │  Tolkien                    │
   │           short/long_term_narrative   │     │    reads context_brief = B_N│   ◀── Spock's nudge from N
   └────────────────┬─────────────────────┘     │    writes beat_{N+1}, ...   │
                    ▼                            └─────────────┬───────────────┘
   ┌──────────────────────────────────────┐                   ▼
   │  Spielberg                            │    ┌────────────────────────────┐
   │    reads beat_N, prev end_frame       │    │  Spielberg                  │
   │    writes current_shot (shot_N)       │    │    reads beat_{N+1},        │
   └────────────────┬─────────────────────┘    │           end_frame from    │
                    ▼                            │           history[-1] (= N) │
   ┌──────────────────────────────────────┐    └─────────────┬───────────────┘
   │  Attenborough                         │                  ▼
   │    reads beat_N, shot_N, narr_memory │    ┌────────────────────────────┐
   │    writes commentary_N                │    │  Attenborough               │
   │    (TTS side-effect → audio path)    │    │    reads beat_{N+1}, ...    │
   └────────────────┬─────────────────────┘    └─────────────┬───────────────┘
                    ▼                                         ▼
   ┌──────────────────────────────────────┐    ┌────────────────────────────┐
   │  Spock                                │    │  Spock                      │
   │    reads beat_N, shot_N, comm_N,     │    │    ...                       │
   │           world_state W_{N-1}, M_{N-1}│   └─────────────┬───────────────┘
   │    writes W_N, M_N, B_N               │                  ▼
   └────────────────┬─────────────────────┘                ... and so on
                    ▼
        runner: history.append(entry_N
                  = beat_N + shot_N + commentary_N + U_N)
```

Plan 03 nuance: Spock *starts* during turn N (as an async task on a
snapshot) but his outputs are *applied* at the join that opens turn N+1 —
the diagram's data flow is unchanged, only the wall-clock overlaps. The
Attenborough box only runs on turns where the silence floor is open; on
held turns the engine writes a silent commentary directly.

What carries forward turn-to-turn:
- `world_state`, `narrative_memory`, `context_brief` — Spock's outputs
- `long_term_narrative`, `short_term_narrative` — Tolkien's outputs
- `history` — appended by the runner (only when beat+shot+commentary all exist)
- Blueprint fields — never change

What is per-turn (overwritten each turn):
- `user_input`, `turn_number`
- `current_beat`, `current_shot`, `current_commentary`, `current_audio_path`

Narrator-clock fields (live producer measures them when audio plays;
the engine ticks story-time otherwise):
- `audio_seconds_owed`, `silence_seconds`, `last_clip_duration`, `pacing_managed`

---

## 5. Quick reference: who reads what from state

Compact view; "blueprint" means any of the read-only init fields.

| Field | Tolkien | Spielberg | Attenborough | Spock |
|---|:---:|:---:|:---:|:---:|
| `user_input` | R | | | |
| `turn_number` | R | R | R | R |
| `current_beat` | (W) | R | R | R |
| `current_shot` | | (W) | R | R |
| `current_commentary` | | | (W) | R |
| `world_state` | R | R | | R / W |
| `narrative_memory` | R | | R | R / W |
| `context_brief` | R | | | (W) |
| `long_term_narrative` | R / W | | | |
| `short_term_narrative` | R / W | | R | |
| `history` (last N) | R (3, narration only) | R (last end_frame) | R (N, commentary; narrations since last line) | R (N, full) |
| `tone_guidelines` | | | R | |
| `visual_style` | | R | | |
| `locations`, `characters` | (protagonist only) | R | | R |
| `world_constraints`, `narrative_premise` | R | | | |
| Pacing fields | | | R (silence label) | |

R = read, W = write, (W) = wrote earlier this turn. The pacing *gate*
itself lives in the turn engine, not in Attenborough.

---

## 6. Tracing (Langfuse)

LLM calls are traced through the **`langfuse.openai` drop-in wrapper**
imported in `src/llm/openai_backend.py`. Every call captures model,
prompts, completions, token usage, latency, and cost — no agent code
knows about Langfuse.

**Activation.** Tracing turns on automatically when `LANGFUSE_PUBLIC_KEY`
is set in the environment (see `.env.example` for the EU-cloud defaults).
With keys absent the wrapper acts as a pure passthrough and the helper
in `src/util/langfuse_setup.py` no-ops.

**Trace shape.** Each turn becomes one Langfuse trace named `turn_<n>`
with one child generation per agent call. Spans are opened around
`graph.ainvoke(state)` (the turn engine) in `src/eval/runner.py`. The
async Spock task inherits the turn's span via contextvars, so his
generation attributes to the turn it describes even though it completes
during the next one; Attenborough only appears on turns where the
silence floor was open. All turns of one run share a session id
mirroring the on-disk log filename stem, so a Langfuse session and the
local `logs/<session>.json` / `.md` files cross-reference 1:1.

```
Session: mas_demo_scenario_20260505_120000
  tags: [mas, scenario, demo_scenario]
├── Trace: turn_1
│   ├── Generation: tolkien        (prompt, completion, tokens, latency)
│   ├── Generation: spielberg
│   ├── Generation: attenborough   (only when the narrator clock fired)
│   └── Generation: spock          (async; completes during turn_2)
├── Trace: turn_2
└── ...
```

**Tags per loop:** `[<config.name>, "scenario", <stem>]` for `play
--scenario`, `[<config.name>, "play"]` / `"live"` / `"live_text"` for
the others.

**Why `StoryLogger` stays.** Langfuse is call-level observability, not
narrative reconstruction. The Markdown transcript in `logs/*.md` is the
canonical "read this run as a story" artifact for human review.

**LLM-as-judge.** The post-hoc scoring pipeline lives in
`src/eval/judge.py` — kept as reusable A/B-evaluation infrastructure.
It pulls a finished session via `langfuse_fetch.fetch_session(...)`,
normalises a run's per-agent outputs into a single `{beat, shot,
commentary, memory}` shape, runs a judge model over the per-turn and
per-window rubrics defined in `src/prompts/judge.*.md`, and writes
everything back as Langfuse `Score` objects keyed to each turn's
`trace_id`. Score names are prefixed `local.*` (per-turn rubric) or
`window.*` (per-window memory rubric) so dashboards filter cleanly. See
`docs/findings/judge-methodology.md` for the methodology and rubric
rationale.

**Operational stats.** `python main.py stats <session_id>` mines a
finished session's traces into a terminal dashboard: per-agent cost /
latency / tokens / parse-failures, per-turn cost+latency trend (bucketed
for long sessions, so context-growth drift is visible), and any
WARNING/ERROR events. `--json` emits the full breakdown for scripting.
Lives in `src/eval/stats.py`.

**Common gotcha:** short-lived processes (one-shot scenario runs) lose
buffered events without a flush. Every runner finally-block calls
`langfuse_setup.flush()`.

---

## 7. Where to look in code

- `src/state/story_state.py` — `StoryState`, `HistoryEntry`,
  `apply_world_delta`, `initialize`
- `src/models/responses.py` — `Beat`, `Shot`, `Commentary`,
  `WorldStateDelta`, `MemoryUpdate`
- `src/agents/tolkien.py` / `spielberg.py` / `attenborough.py` /
  `spock.py` — each `run(state, llm, config, logger, ...)`
  function; reads are the `load_prompt(...)` keyword args
- `src/agents/_common.py` — `call_llm_structured` and the state→prompt
  formatters
- `src/graph/mas_graph.py` — node wiring
- `src/eval/runner.py` — `_commit_history`, `_coerce_state`, the loops
  that drive turns; per-turn `turn_span(...)` and `langfuse_flush()`
- `src/util/langfuse_setup.py` — `is_enabled`, `turn_span`, `flush`
- `src/llm/openai_backend.py` — Langfuse drop-in import + trace kwargs
- `src/prompts/*.md` — the actual prompt templates each agent fills in
