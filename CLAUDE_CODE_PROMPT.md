# CLAUDE_CODE_PROMPT.md

Copy everything below the line as your Claude Code prompt.

---

## Project Context

You are building **interactive-mas** — a multi-agent system (MAS) for interactive storytelling, built with LangGraph. Users type commands to steer a protagonist through a story. Agents collaborate behind the scenes to generate coherent narrative.

This is an academic project for IKT469 (Deep Neural Networks) at the University of Agder. It has two purposes:
1. A working interactive storytelling system with a terminal UI
2. A benchmark comparing different agent configurations, evaluated post-hoc from session logs

## Read These First

Before writing any code, read ALL of these:
- `CLAUDE.md` — your working instructions (plan mode, verification, task management)
- `README.md` — overview and structure
- `ARCHITECTURE.md` — state schema, agent specs, graph topology, context management, prompt template pattern, JSON sanitizer, interaction logger
- `BENCHMARK.md` — experiment matrix, evaluation approach
- `story.json` — the story blueprint (setting, protagonist, rules, narrative premise)
- `test_scenario.json` — the 100-turn benchmark scenario

These are the source of truth. Follow them closely.

## Reference Implementations

The `reference/` folder contains implementations of `json_sanitizer.py` and `interaction_logger.py` from a previous project. These are battle-tested but written for a different context.

**Study them critically before using:**
- Understand the patterns and logic
- Remove anything this project doesn't need
- Adapt to this project's models and conventions
- Keep them as clean and minimal as possible
- Don't copy blindly

## Key Patterns

1. **Pydantic v2 BaseModel everywhere** — state, configs, story, scenarios, and LLM response schemas are all Pydantic models. Not TypedDict, not raw dicts.

2. **Prompt templates as .md files** — agent prompts live in `src/prompts/` as Markdown files with `{variable}` placeholders. Loaded at runtime via `src/util/prompt_loader.py` using a simple `lru_cache` + `.format(**kwargs)` pattern.

3. **JSON sanitizer pipeline** — LLMs (especially local Gemma) will produce malformed JSON. Sheldon (Memory) and Chekhov (Threads) both emit JSON and share this pipeline. Parse strategy: try direct parse → extract → repair → skip and log.

4. **Interaction logger** — every LLM call gets logged to `logs/` as structured JSON. Each session/run gets its own file. This is the primary benchmark output — evaluation happens post-hoc from these logs.

5. **Text-first agents** — most agents receive and return plain text. Only Sheldon (Memory) and Chekhov (Threads) output structured JSON — Sheldon for entities/inventory, Chekhov for open narrative setups. Context is context; prose carries the same information as structured dicts for LLMs.

6. **Story blueprint** — the story world is defined once in `story.json` with setting, protagonist, narrative premise, `world_constraints`, and `tone_guidelines`. These two lists are NOT broadcast to every agent — each agent only receives the subset relevant to its role (see agent specs). The scenario JSON only contains user commands.

## Tech Stack

- **Python 3.10+**
- **LangGraph** — graph-based multi-agent orchestration
- **Pydantic v2** — all models (state, config, story, scenario, responses)
- **OpenAI SDK** — HTTP client for both vLLM (Gemma 4) and OpenAI API (GPT-4o)
- **Rich** — terminal UI
- **vLLM** — serves Gemma 4 locally on `localhost:8000` (started separately by the user)
- **PyYAML** — configs

## What to Build

### Phase 1: Core Infrastructure

**Pydantic models** (`src/state/story_state.py`, `src/models/`):
- `StoryState` in `src/state/story_state.py` — the shared state as defined in ARCHITECTURE.md. Lean, text-first. Includes story blueprint fields (setting, rules, premise) set once at initialization.
- `Story`, `Protagonist` in `src/models/story.py` — loaded from `story.json`. Includes `narrative_premise: str`, `world_constraints: list[str]`, and `tone_guidelines: list[str]`.
- `Config` in `src/models/config.py` — loaded from YAML
- `MemoryUpdate`, `NarrativeThread`, and `ThreadUpdate` in `src/models/responses.py` — structured output schemas for Sheldon (Memory) and Chekhov (Threads)

**Utility modules** (`src/util/`):
- `prompt_loader.py` — load and format .md prompt templates (see ARCHITECTURE.md)
- `json_sanitizer.py` — JSON repair pipeline. Adapt from `reference/json_sanitizer.py`
- `interaction_logger.py` — log every LLM call to `logs/`. Adapt from `reference/interaction_logger.py`

**LLM backends** (`src/llm/`):
- `base.py` — abstract `LLMBackend` with `async generate(messages, temperature, max_tokens) -> str`. Should also return token usage.
- `gemma.py` — calls vLLM on `localhost:8000/v1/chat/completions` using the `openai` SDK with `base_url="http://localhost:8000/v1"`. Model from config.
- `openai_backend.py` — calls OpenAI API. Key from `OPENAI_API_KEY` env var.

Both backends: handle errors gracefully, return token usage, async, log via interaction_logger.

**Configs** (`configs/`):
YAML files for each of the 3 experiment configurations. Load into the `Config` Pydantic model.

### Phase 2: Prompts

Create all prompt templates in `src/prompts/`:

Each agent gets a `system.md` and `user.md`. The user template uses `{variable}` placeholders filled at call time.

**Critical:** Blueprint constraints are split across the MAS pipeline. Tolkien gets `world_constraints` (and is instructed to respect them upfront) but NOT `tone_guidelines`. Wilde gets ONLY `tone_guidelines`. There is no dedicated consistency agent — Tolkien owns rule compliance.

**Solo is the exception:** the single_llm prompt receives the full blueprint (setting, protagonist, premise, `world_constraints`, `tone_guidelines`) because it has no helper agents. Solo is a baseline — a well-briefed single LLM.

- `narrator.system.md` / `narrator.user.md` — creative writing, user agency, story advancement. Receives setting, premise, `world_constraints`, `open_threads` (live-only, prose-rendered), and all context as prose. Include the "do not force payoffs — slow burn is fine" instruction alongside the threads list, and a self-check instruction to respect `world_constraints` and avoid contradicting `world_state` / recent beats.
- `editor.system.md` / `editor.user.md` — polishes draft narration for LEGO-Movie tone. Receives `tone_guidelines`.
- `memory.system.md` / `memory.user.md` — structured JSON extraction of world state updates. The user prompt should explicitly include the `MemoryUpdate` JSON schema so the LLM knows what structure to return.
- `threads.system.md` / `threads.user.md` — structured JSON extraction of open narrative threads (new setups introduced + existing threads paid off). The user prompt should explicitly include the `ThreadUpdate` JSON schema. Emphasize: "a thread is closed ONLY when the narration explicitly references its payoff or outcome. When in doubt, leave it open."
- `single_llm.system.md` / `single_llm.user.md` — one agent does everything

### Phase 3: Agents

Each agent is an async function: `async def agent_name(state: StoryState, llm: LLMBackend, config: Config, logger: InteractionLogger) -> dict`

Returns a partial state dict that LangGraph merges.

**Tolkien — Narrator** (`narrator.py`):
- Receives prose context: setting, narrative premise, `world_constraints`, summary, world state, open threads (live-only), recent beats, user input
- Does NOT receive `tone_guidelines`
- Writes: `current_narration` (draft in full_cast; final in core/solo)
- Prompt includes an explicit self-check instruction: respect world_constraints, do not contradict world_state or recent beats

**Wilde — Editor** (`editor.py`):
- Polishes Tolkien's draft for LEGO-Movie tone (light touch, no rewrite)
- Receives prose context: draft narration, tone guidelines
- Writes: `current_narration` (overwrites draft)
- Only included in `full_cast`

**Sheldon — Memory** (`memory.py`):
- One of two structured-output agents (Chekhov is the other)
- Returns a `MemoryUpdate` parsed via the json_sanitizer pipeline
- MERGES updates into existing `world_state` — never overwrites unmentioned fields
- Every `summary_interval` turns, also generates a compressed `summary`

**Chekhov — Threads** (`threads.py`):
- The other structured-output agent
- Returns a `ThreadUpdate` parsed via the json_sanitizer pipeline (same infrastructure Sheldon uses)
- Receives prose context: current narration, current open threads list, recent beats
- Writes: updated `open_threads` — appends `new_threads`, moves IDs in `close_threads` from open to closed, populates `payoff_summary` from `payoff_summaries`
- MERGES updates — threads Chekhov doesn't mention stay unchanged
- Closes a thread ONLY when the narration explicitly references its payoff. When in doubt, leaves it open
- Stable IDs are load-bearing: Chekhov sees the current list in its prompt and must reuse existing IDs when closing/updating, only minting new IDs for genuinely new threads
- Runs in parallel with Sheldon on the polished narration
- Only included in `full_cast`

**All agents:**
- Load prompts via `prompt_loader`
- Log every call via `interaction_logger`
- Target ~4-8K tokens per call

### Phase 4: Graphs

**Solo** (`solo_graph.py`) — 1 agent:
```
Input → Single Agent → Output
```

**Core** (`core_graph.py`) — 2 agents:
```
Input → Tolkien → Sheldon → Output
```

**Full Cast** (`full_cast_graph.py`) — 4 agents:
```
Input
  → Tolkien (draft, respects world_constraints)
  → Wilde (polish)
  ─┬─→ Sheldon (memory) ─┐
   │                     ├─→ Output
   └─→ Chekhov (threads)─┘
```
Sheldon and Chekhov run in parallel on the polished narration. No retry loop — Tolkien handles rule compliance upfront.

Note: LangGraph wants TypedDict for state, but agents work with Pydantic models internally. Bridge this by converting at the graph boundary — `state.model_dump()` to pass in, `StoryState(**state_dict)` to reconstruct. Or use LangGraph's Pydantic state support if available.

### Phase 5: Terminal UI

(`src/ui/terminal.py`)

**Single flowing story.** Like reading a book. No panels, no sections, no slash commands.

- Rich for styled text
- Each turn: display narration as continuous flowing text
- Simple input prompt after each narration
- On startup: story setting and protagonist info, or a brief intro for free play
- Ctrl+C to quit

### Phase 6: Benchmark Runner

(`src/eval/runner.py`)

Run the scenario through a config. For each of the 100 turns: feed the user command, run the graph, log everything via interaction_logger. Save the full session log to `logs/`.

No judge, no metrics, no report generation. Just run and log. Evaluation happens afterwards.

### Phase 7: Entry Point

**`main.py`** with argparse:
- `play` — interactive (optional `--scenario`)
- `benchmark` — run the scenario against all configs, log everything

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
- **Prompt templates as .md files** — never hardcode prompts as Python strings
- **Constraints are split, not shared** — `world_constraints` only to Tolkien; `tone_guidelines` only to Wilde
- **JSON sanitizer for Sheldon's and Chekhov's output** — always go through the pipeline
- **Log every LLM call** — via interaction_logger
- **Text-first** — most agents work with prose, not structured data
- Async everywhere, type hints everywhere
- `logging` module, not print
- Errors handled gracefully
- Functions over classes where possible (agents are functions)
- Follow the project structure exactly

## What NOT to Build

- No web frontend
- No database
- No Docker
- No vLLM management
- No model fine-tuning
- No automated judge or scoring pipeline
- No matplotlib reports

## Build Order

1. Pydantic models (state, config, story, responses — including MemoryUpdate and ThreadUpdate)
2. Utility modules (prompt_loader, json_sanitizer, interaction_logger)
3. LLM backends
4. Prompt templates (.md files)
5. Tolkien (Narrator) alone → verify it works with a manual test
6. Add Sheldon (Memory) → verify (completes Core)
7. Add Wilde (Editor) and Chekhov (Threads) → verify (completes Full Cast)
9. Build all 3 graph variants
10. Terminal UI
11. Benchmark runner
12. Main entry point

Test each step before moving on.
