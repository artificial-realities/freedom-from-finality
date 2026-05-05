# Agent Flow

A focused walkthrough of the MAS pipeline: which agent runs when, what each
agent reads from `StoryState`, and what it writes back. For the wider design
rationale, see `ARCHITECTURE.md`; this doc is the short, hands-on reference.

The single source of truth for shape is `src/state/story_state.py` and
`src/models/responses.py`. The graph wiring is `src/graph/mas_graph.py`.

---

## 1. The pipeline at a glance

```
                     ┌──────────────┐
   user_input ─────▶ │   Tolkien    │  beat writer  ────────▶ current_beat
                     └──────┬───────┘                          long_term_narrative
                            │                                  short_term_narrative
                            ▼
                     ┌──────────────┐
                     │  Spielberg   │  shot composer ────────▶ current_shot
                     └──────┬───────┘
                            │
                            ▼
                     ┌──────────────┐
                     │ Attenborough │  voiceover    ────────▶ current_commentary
                     └──────┬───────┘  (+ TTS side-effect)    current_audio_path
                            │
                            ▼
                     ┌──────────────┐
                     │    Spock     │  memory       ────────▶ world_state
                     └──────┬───────┘                          narrative_memory
                            │                                  context_brief
                            ▼
                     end of turn ─── runner appends HistoryEntry
```

Strictly sequential, no retries, no branches. Drift is caught the *next* turn
via Spock's `context_brief`, which Tolkien reads at the top of turn N+1.

The **solo** baseline collapses all four agents into one node that emits
`Beat + Shot + Commentary + MemoryUpdate` in a single `SoloResponse`.

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
| **Pacing** (live producer only) | `audio_seconds_owed`, `silence_seconds`, `last_clip_duration`, `pacing_managed` | Set by the live producer post-render; benchmark/play loops leave them at 0 |
| **History** | `history: list[HistoryEntry]` | Appended by the runner after Spock, once beat+shot+commentary all exist |
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

### 3.3 Attenborough — voiceover commentator
**Purpose.** Decide whether to speak this turn, and what to say.

**Reads from state**
- Blueprint: `tone_guidelines`
- Beat: `current_beat.narration / .action / .outcome`
- Direction: `short_term_narrative`
- Shot: `camera`, `motion`, `end_frame_description`, `duration_seconds`
- Memory: `narrative_memory`
- Recent: last *N* commentary lines (`config.context_window_history`)
- Pacing: a humanized `silence_label` derived from `silence_seconds`

**Writes**
- `current_commentary: Commentary` (empty `voiceover` = stay silent)
- `current_audio_path` (set when `audio_enabled` and TTS succeeds)

**Pacing gates** (only when `pacing_managed=True` *and* `audio_enabled=True`):
1. `audio_seconds_owed > 0.001` → return empty commentary, log
   `attenborough_hold` reason `audio_owed`.
2. `silence_seconds < config.min_pause_seconds` → return empty, log
   reason `min_pause`.

In benchmark/`play` loops the producer does not bookkeep pacing, so these
gates are bypassed and the agent always reaches the LLM.

**TTS side effect.** When `audio_enabled` and a `tts` backend is wired,
the runtime synthesizes `voiceover` to a file and stores its path on
`current_audio_path`. Failures never block the turn.

---

### 3.4 Spock — memory & context curator
**Purpose.** Apply this turn's effects to memory and produce the
`context_brief` that lands in Tolkien's prompt next turn.

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

### 3.5 Solo — monolithic baseline
One agent, one LLM call, emits `SoloResponse = Beat + Shot + Commentary +
MemoryUpdate`. Receives the full blueprint and the entire rolling state.
Honors the same pacing gates as Attenborough so the two configurations
produce comparable commentary streams. Lives in `src/agents/solo.py` and
`src/graph/solo_graph.py`.

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

What carries forward turn-to-turn:
- `world_state`, `narrative_memory`, `context_brief` — Spock's outputs
- `long_term_narrative`, `short_term_narrative` — Tolkien's outputs
- `history` — appended by the runner (only when beat+shot+commentary all exist)
- Blueprint fields — never change

What is per-turn (overwritten each turn):
- `user_input`, `turn_number`
- `current_beat`, `current_shot`, `current_commentary`, `current_audio_path`

What is producer-managed (live mode only):
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
| `history` (last N) | R (3, narration only) | R (last end_frame) | R (N, commentary) | R (N, full) |
| `tone_guidelines` | | | R | |
| `visual_style` | | R | | |
| `locations`, `characters` | (protagonist only) | R | | R |
| `world_constraints`, `narrative_premise` | R | | | |
| Pacing fields | | | R (gates) | |

R = read, W = write, (W) = wrote earlier this turn.

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
with one child generation per agent (Tolkien / Spielberg / Attenborough
/ Spock for MAS, a single `solo` generation for the baseline). Spans are
opened around `graph.ainvoke(state)` in `src/eval/runner.py`. All turns
of one run share a session id mirroring the on-disk log filename stem,
so a Langfuse session and the local `logs/<session>.json` / `.md` files
cross-reference 1:1.

```
Session: mas_test_scenario_20260505_120000
  tags: [mas, scenario, test_scenario]
├── Trace: turn_1
│   ├── Generation: tolkien        (prompt, completion, tokens, latency)
│   ├── Generation: spielberg
│   ├── Generation: attenborough
│   └── Generation: spock
├── Trace: turn_2
└── ...
```

**Tags per loop:** `[<config.name>, "scenario", <stem>]` for benchmarks,
`[<config.name>, "play"]` / `"live"` / `"live_text"` for the others.

**Why `StoryLogger` stays.** Langfuse is call-level observability, not
narrative reconstruction. The Markdown transcript in `logs/*.md` is the
canonical "read this run as a story" artifact for human review.

**LLM-as-judge.** The post-hoc scoring pipeline lives in
`src/eval/judge.py`. It pulls a finished session via
`langfuse_fetch.fetch_session(...)`, normalises MAS and solo outputs
into a single `{beat, shot, commentary, memory}` shape, runs Claude
Sonnet 4.6 over the per-turn and per-window rubrics defined in
`src/prompts/judge.*.md`, and writes everything back as Langfuse
`Score` objects keyed to each turn's `trace_id`. Score names are
prefixed `local.*` (per-turn rubric) or `window.*` (per-window memory
rubric) so dashboards filter cleanly. See `docs/BENCHMARK.md` for the
full evaluation methodology, including the human spot-check calibration
that backs the judge.

**Common gotcha:** short-lived processes (one-shot benchmarks) lose
buffered events without a flush. Every runner finally-block calls
`langfuse_setup.flush()`.

---

## 7. Where to look in code

- `src/state/story_state.py` — `StoryState`, `HistoryEntry`,
  `apply_world_delta`, `initialize`
- `src/models/responses.py` — `Beat`, `Shot`, `Commentary`,
  `WorldStateDelta`, `MemoryUpdate`, `SoloResponse`
- `src/agents/tolkien.py` / `spielberg.py` / `attenborough.py` /
  `spock.py` / `solo.py` — each `run(state, llm, config, logger, ...)`
  function; reads are the `load_prompt(...)` keyword args
- `src/agents/_common.py` — `call_llm_structured` and the state→prompt
  formatters
- `src/graph/mas_graph.py` / `solo_graph.py` — node wiring
- `src/eval/runner.py` — `_commit_history`, `_coerce_state`, the loops
  that drive turns; per-turn `turn_span(...)` and `langfuse_flush()`
- `src/util/langfuse_setup.py` — `is_enabled`, `turn_span`, `flush`
- `src/llm/openai_backend.py` — Langfuse drop-in import + trace kwargs
- `src/prompts/*.md` — the actual prompt templates each agent fills in
