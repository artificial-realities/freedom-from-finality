# CLAUDE_CODE_PROMPT.md

Copy everything below the line as your Claude Code prompt.

---

## Project Context

You are building **interactive-mas** — a multi-agent system (MAS) for interactive storytelling, built with LangGraph. The story plays as a flowing sequence of ~5-second video clips chained via image-to-video (i2v), with the user optionally steering the story between clips through natural-language commands.

This is an academic project for IKT469 (Deep Neural Networks) at the University of Agder. It has two purposes:

1. A working interactive storytelling system with a terminal UI.
2. A benchmark comparing a single well-briefed LLM against a 3-agent MAS, evaluated post-hoc from session logs.

## Read These First

Before writing any code, read ALL of these:

- `CLAUDE.md` — your working instructions
- `README.md` — overview and structure
- `ARCHITECTURE.md` — design philosophy, agent specs, state schema, turn execution order, pipeline buffer, one-turn-delayed feedback loop
- `BENCHMARK.md` — research question, experiment matrix, evaluation approach
- `story.json` — the story blueprint (synopsis, visual_style, locations, characters, rules, premise, narrative directions)
- `test_scenario.json` — the 100-turn benchmark scenario

These are the source of truth. Follow them closely.

## Reference Implementations

The `reference/` folder contains:

- `json_sanitizer.py` and `interaction_logger.py` — battle-tested utilities from a previous project. Study, keep only what this project needs, adapt to this project's models and conventions. Do not copy blindly.
- `blueprint.json` and `narratron.system.md` — prior-project blueprint and narrator prompt. Use as stylistic inspiration for the new blueprint and prompts; do not carry over panel/comic-specific structure.
- `i2v_chaining_test.ipynb`, `wan_test.ipynb`, `wan2.2_i2v_local_test.ipynb` — i2v chaining experiments. Context only; not used by the runtime code in this project.

## Key Patterns

1. **Pydantic v2 BaseModel everywhere** — state, configs, story, and all LLM response schemas are Pydantic models. Not TypedDict, not raw dicts.

2. **Prompt templates as .md files** — agent prompts live in `src/prompts/` as Markdown files with `{variable}` placeholders. Loaded at runtime via `src/util/prompt_loader.py` using `lru_cache` + `.format(**kwargs)`.

3. **All three agents emit structured output.** Tolkien emits `Beat`, Spielberg emits `Shot`, Supervisor emits `MemoryUpdate`. Solo emits all three in a single structured response. Every structured response passes through the `json_sanitizer` repair pipeline.

4. **JSON sanitizer pipeline** — local Gemma 4 will occasionally produce malformed JSON. Parse strategy: try direct parse → extract → repair → skip and log.

5. **Interaction logger** — every LLM call gets logged to `logs/` as structured JSON. Each session gets its own file. This is the primary benchmark output — evaluation happens post-hoc from these logs.

6. **Story blueprint** — the story is defined once in `story.json` with `title`, `synopsis`, `visual_style`, `locations[]`, `characters[]`, `world_constraints[]`, `narrative_premise`, `long_term_narrative`, and `short_term_narrative`. Each field has a primary audience (see ARCHITECTURE.md) — agents only see the subset their role needs.

7. **One-turn-delayed feedback loop** — Supervisor's state updates from turn N are read by Tolkien at turn N+1. Never within the same turn. This is load-bearing: it keeps the graph strictly forward and prevents self-reinforcing drift.

8. **Pipeline buffer (when video is live)** — the MAS runs ~6 clips ahead of the viewer. User input enters a queue and applies to the next unrendered clip. Story must keep flowing on silent turns. Video generation is opt-out; benchmark mode bypasses the buffer and runs synchronously.

## Tech Stack

- **Python 3.10+**
- **LangGraph** — graph-based multi-agent orchestration
- **Pydantic v2** — all models
- **OpenAI SDK** — HTTP client for both vLLM (Gemma 4) and OpenAI API (GPT-4o)
- **Rich** — terminal UI
- **vLLM** — serves Gemma 4 locally on `localhost:8000` (started separately by the user)
- **PyYAML** — configs

## What to Build

### Phase 1: Core Infrastructure

**Pydantic models**:

- `src/state/story_state.py` — `StoryState` as defined in ARCHITECTURE.md, plus `HistoryEntry`. Lean, text-first. Includes blueprint fields set once at initialization.
- `src/models/story.py` — `Story`, `Location`, `Character`. Loaded from `story.json`.
- `src/models/config.py` — `Config` loaded from YAML.
- `src/models/responses.py` — `Beat`, `Shot`, `WorldStateDelta`, `MemoryUpdate`. (No dialogue field — this project's protagonist does not speak.)

**Utility modules** (`src/util/`):

- `prompt_loader.py` — load and format .md prompt templates.
- `json_sanitizer.py` — JSON repair pipeline. Adapt from `reference/json_sanitizer.py`.
- `interaction_logger.py` — log every LLM call to `logs/`. Adapt from `reference/interaction_logger.py`.

**LLM backends** (`src/llm/`):

- `base.py` — abstract `LLMBackend` with `async generate(messages, temperature, max_tokens) -> tuple[str, dict]`. Returns `(response_text, token_usage)`.
- `gemma.py` — calls vLLM on `localhost:8000/v1/chat/completions` using the `openai` SDK with `base_url="http://localhost:8000/v1"`. Model from config.
- `openai_backend.py` — calls OpenAI API. Key from `OPENAI_API_KEY` env var.

Both backends: handle errors gracefully, return token usage, async, log via `interaction_logger`.

**Configs** (`configs/`):

- `solo.yaml` — single LLM, fully briefed.
- `mas.yaml` — three agents.

Both load into the `Config` Pydantic model.

### Phase 2: Prompts

Create all templates in `src/prompts/`. Each agent gets a `system.md` and `user.md`. The user template uses `{variable}` placeholders filled at call time.

**Critical: blueprint fields are split by audience.** See ARCHITECTURE.md for the full table. Briefly:

- **Tolkien** gets `narrative_premise`, `world_constraints`, `long_term_narrative`, `short_term_narrative`, Supervisor's `context_brief`, location **names only**, character **names + one-line summary only**, and `user_input`. Does NOT get `visual_style` or full descriptions.
- **Spielberg** gets `visual_style`, full `locations[]`, full `characters[]`, Tolkien's `Beat`, previous clip's `end_frame_description`, current `protagonist_location`.
- **Supervisor** gets `current_beat`, `current_shot`, current `world_state`, current `narrative_memory`, and recent history.
- **Solo** gets the entire blueprint plus the full rolling state and recent history — in one structured call that emits all three response shapes.

Prompt file list:

- `narrator.system.md` / `narrator.user.md` — creative beat writing (action + outcome, no dialogue). Include the self-check: respect `world_constraints`, do not contradict `context_brief`. Instruct Tolkien to advance on `short_term_narrative` when `user_input` is empty, and to actively look for callback opportunities when props or bits from earlier in the run would fit.
- `director.system.md` / `director.user.md` — i2v shot composition. Instruct Spielberg to re-anchor on the locked blueprint descriptors every turn (this is how visual consistency survives long runs). Must describe continuity from the previous `end_frame_description` and produce a new `end_frame_description` for the next turn. When introducing a new prop, pick either the **summon** or **walk-in-from-offscreen** entry pattern and reflect it in the prompt.
- `supervisor.system.md` / `supervisor.user.md` — structured `MemoryUpdate`. Emphasize: `world_state_delta` MERGES (unmentioned fields preserved). `narrative_memory` is rolling prose, compressed older / detailed recent, bounded around `narrative_memory_target_tokens` from config. `context_brief` is the filtered slice for Tolkien's next turn — deliberately lean.
- `single_llm.system.md` / `single_llm.user.md` — one agent produces `Beat` + `Shot` + `MemoryUpdate` in a single structured response. Fully briefed.

Each prompt ends with a plain-text description of the structured output fields the agent must produce (not a raw JSON schema dump).

### Phase 3: Agents

Each agent is an async function:

```python
async def agent_name(state: StoryState, llm: LLMBackend, config: Config, logger: InteractionLogger) -> dict
```

Returns a partial state dict that the graph merges.

**Tolkien — Narrator** (`src/agents/narrator.py`):

- Reads from `state`: `narrative_premise`, `world_constraints`, `long_term_narrative`, `short_term_narrative`, `context_brief`, location names, character summaries, `user_input`.
- Writes: `current_beat` (`Beat`), updates `short_term_narrative`, optionally `long_term_narrative`.
- Advances on `short_term_narrative` when `user_input` is empty — never stalls.

**Spielberg — Director** (`src/agents/director.py`):

- Reads from `state`: `visual_style`, full `locations`, full `characters`, `current_beat`, previous `Shot.end_frame_description` (from `history[-1].shot` if present), `world_state.protagonist_location`.
- Writes: `current_shot` (`Shot`).
- Re-anchors on blueprint descriptors every turn for visual consistency.

**Supervisor — Memory and Curator** (`src/agents/supervisor.py`):

- Reads from `state`: `current_beat`, `current_shot`, `world_state`, `narrative_memory`, recent history.
- Writes: merged `world_state`, new `narrative_memory`, new `context_brief`.
- MERGES `world_state_delta` into `world_state` — unmentioned fields preserved. `inventory` is `None` for unchanged, list for replacement.

**All agents:**

- Load prompts via `prompt_loader`.
- Log every call via `interaction_logger`.
- Parse structured output via `json_sanitizer`.
- Target per-agent context budgets from ARCHITECTURE.md.

### Phase 4: Graphs

**Solo** (`src/graph/solo_graph.py`) — 1 agent, fully briefed, single structured response carrying `Beat` + `Shot` + `MemoryUpdate`:

```
Input → Solo → (Beat + Shot + MemoryUpdate merged into state) → Output
```

**MAS** (`src/graph/mas_graph.py`) — 3 agents sequential:

```
Input → Tolkien → Spielberg → Supervisor → Output
```

No retry loop. Tolkien handles rule compliance upfront; Supervisor catches drift on the next turn via the one-turn-delayed feedback loop.

LangGraph prefers TypedDict for state, but agents work with Pydantic internally. Bridge at the graph boundary — `state.model_dump()` to pass in, `StoryState(**state_dict)` to reconstruct — or use LangGraph's Pydantic state support if available.

### Phase 5: Terminal UI

(`src/ui/terminal.py`)

A minimal terminal view of the story as it unfolds. Each turn, print a short textual rendering of the current `Beat` (action + any dialogue + outcome) so the user can read along without rendering video. The point is to validate the pipeline, not to be pretty.

- Rich for styling.
- Input prompt between turns — user may type a command or press Enter to let the story advance on its own.
- On startup: display `title`, `synopsis`, and protagonist info.
- Ctrl+C to quit.

### Phase 6: Benchmark Runner

(`src/eval/runner.py`)

Run the scenario through a config. For each of the 100 turns: feed the user command, run the graph synchronously (bypassing any pipeline buffer), log everything via `interaction_logger`. Save the full session log to `logs/`.

No judge, no metrics, no report generation. Just run and log. Evaluation happens afterwards.

### Phase 7: Entry Point

**`main.py`** with argparse:

- `play` — interactive (optional `--scenario` to drive from a file instead of stdin).
- `benchmark` — run the scenario against both configs, log everything.

**`requirements.txt`**:

```
langgraph>=0.2.0
langchain>=0.3.0
langchain-openai>=0.2.0
langchain-community>=0.3.0
openai>=1.0.0
pydantic>=2.0.0
rich>=13.0.0
pyyaml>=6.0
```

## Guidelines

- **Pydantic everywhere** — state, config, story, responses. Not raw dicts.
- **Prompt templates as .md files** — never hardcode prompts as Python strings.
- **Blueprint fields split by audience** — follow the table in ARCHITECTURE.md. Do not hand Tolkien the full `visual_style` or full location/character descriptions.
- **All three MAS agents + solo produce structured output** — always go through the json_sanitizer pipeline.
- **One-turn-delayed feedback loop** — Tolkien reads Supervisor's previous turn's `context_brief`, never this turn's.
- **Story must keep flowing when the user is silent** — Tolkien advances on `short_term_narrative`.
- **Log every LLM call** — via `interaction_logger`.
- Async everywhere, type hints everywhere.
- `logging` module, not print.
- Errors handled gracefully — a failed parse skips that turn's update rather than crashing.
- Functions over classes where possible (agents are async functions).
- Follow the project structure exactly.

## What NOT to Build

- No web frontend.
- No database.
- No Docker.
- No vLLM management (user starts it separately).
- No model fine-tuning.
- No automated judge or scoring pipeline.
- No matplotlib reports.
- No live video generation in the benchmark path (runtime-only, opt-out).

## Build Order

1. Pydantic models (state, config, story, responses — `Beat`, `Shot`, `MemoryUpdate`, `WorldStateDelta`). No dialogue / `Line` model — the protagonist does not speak.
2. Utility modules (`prompt_loader`, `json_sanitizer`, `interaction_logger`).
3. LLM backends.
4. Prompt templates (.md files).
5. Tolkien alone → verify with a manual single-turn test.
6. Add Spielberg → verify two-agent output.
7. Add Supervisor → verify full MAS loop with Supervisor's `context_brief` feeding Tolkien's next turn.
8. Build the solo graph (single structured response).
9. Build the MAS graph.
10. Terminal UI.
11. Benchmark runner.
12. Main entry point.

Test each step before moving on.
