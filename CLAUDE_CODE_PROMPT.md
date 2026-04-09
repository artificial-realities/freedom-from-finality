# CLAUDE_CODE_PROMPT.md

Copy everything below the line as your Claude Code prompt.

---

## Project Context

You are building **interactive-mas** — a multi-agent system (MAS) for interactive storytelling, built with LangGraph. Users type commands to steer a protagonist through a story. Agents collaborate behind the scenes to generate coherent narrative.

This is an academic project for IKT469 (Deep Neural Networks) at the University of Agder. It has two purposes:
1. A working interactive storytelling system with a terminal UI
2. A benchmark comparing MAS configurations against a single-LLM baseline, scored by an LLM-as-judge

## Read These First

Before writing any code, read ALL of these:
- `CLAUDE.md` — your working instructions (plan mode, verification, task management)
- `README.md` — overview and structure
- `ARCHITECTURE.md` — state schema, agent specs, graph topology, context management, prompt template pattern, JSON sanitizer, interaction logger
- `BENCHMARK.md` — experiment matrix, metrics, judge protocol
- `scenarios/*.json` — 5 predefined 50-turn scripts

These are the source of truth. Follow them closely.

## Key Patterns (from Comic Chaos)

This project adopts several proven patterns from the Comic Chaos project (github.com/sbfroy/ComicChaos). Study ARCHITECTURE.md for details, but the critical ones are:

1. **Pydantic v2 BaseModel everywhere** — state, configs, scenarios, and LLM response schemas are all Pydantic models. Not TypedDict, not raw dicts. This gives you validation, serialization, and type safety for free.

2. **Prompt templates as .md files** — agent prompts live in `src/prompts/` as Markdown files with `{variable}` placeholders. Loaded at runtime via `src/util/prompt_loader.py` using a simple `lru_cache` + `.format(**kwargs)` pattern. This keeps prompts readable and editable without touching Python code.

3. **JSON sanitizer pipeline** — LLMs (especially local Gemma) will produce malformed JSON. Build `src/util/json_sanitizer.py` with: `sanitize_json_string()` for pre-parse cleanup, `extract_json()` to find first `{` to last `}`, `repair_json()` to truncate at last valid value boundary, and `sanitize_parsed_response()` to deep-clean all strings post-parse. Parse strategy: try direct parse → extract → repair → skip and log.

4. **Interaction logger** — every LLM call gets logged to `logs/` as structured JSON. Each session/run gets its own file. Log: agent type, turn number, timestamp, model, parameters, full prompt (system + user), raw response, parsed response, token usage, latency. This is essential for debugging and benchmark analysis.

5. **Config/scenario loading via Pydantic** — YAML configs and JSON scenarios are loaded into validated Pydantic models with classmethods like `Config.from_yaml(path)` and `Scenario.from_json(path)`.

## Tech Stack

- **Python 3.10+**
- **LangGraph** — graph-based multi-agent orchestration
- **Pydantic v2** — all models (state, config, scenario, responses)
- **OpenAI SDK** — HTTP client for both vLLM (Gemma 4) and OpenAI API (GPT-4o)
- **Rich** — terminal UI
- **vLLM** — serves Gemma 4 locally on `localhost:8000` (started separately by the user)
- **PyYAML** — configs
- **Matplotlib** — benchmark plots

## What to Build

### Phase 1: Core Infrastructure

**Pydantic models** (`src/state/story_state.py`, `src/models/`):
- `StoryState` — the shared state as defined in ARCHITECTURE.md. Use Pydantic BaseModel, not TypedDict. Include helper methods like `get_recent_beats(n)` and `initialize_from_scenario()`.
- `Config` in `src/models/config.py` — loaded from YAML
- `Scenario`, `Protagonist`, `Turn` in `src/models/scenario.py` — loaded from JSON
- `MemoryUpdate`, `ConsistencyCheck` in `src/models/responses.py` — structured output schemas for Memory and Consistency agents

**Utility modules** (`src/util/`):
- `prompt_loader.py` — load and format .md prompt templates (see ARCHITECTURE.md for the exact implementation)
- `json_sanitizer.py` — JSON repair pipeline: `sanitize_json_string`, `extract_json`, `repair_json`, `sanitize_text`, `sanitize_parsed_response`. Port the logic from Comic Chaos's `json_sanitizer.py`.
- `interaction_logger.py` — log every LLM call to `logs/` as structured JSON per session/run

**LLM backends** (`src/llm/`):
- `base.py` — abstract `LLMBackend` with `async generate(messages, temperature, max_tokens) -> str`. Should also return token usage.
- `gemma.py` — calls vLLM on `localhost:8000/v1/chat/completions` using the `openai` SDK with `base_url="http://localhost:8000/v1"`. Model: `google/gemma-4-31b-it`
- `openai_backend.py` — calls OpenAI API with `gpt-4o`. Key from `OPENAI_API_KEY` env var.

Both backends: handle errors gracefully, return token usage, async, log via interaction_logger.

**Configs** (`configs/`):
YAML files for each of the 5 experiment configurations. Load into the `Config` Pydantic model.

### Phase 2: Prompts

Create all prompt templates in `src/prompts/`:

Each agent gets a `system.md` and `user.md`. The user template uses `{variable}` placeholders that are filled at call time with data from state.

- `narrator.system.md` / `narrator.user.md` — creative writing, user agency, story advancement
- `consistency.system.md` / `consistency.user.md` — analytical contradiction detection
- `director.system.md` / `director.user.md` — cinematic scene description for future I2V
- `memory.system.md` / `memory.user.md` — structured JSON extraction of world state updates
- `single_llm.system.md` / `single_llm.user.md` — one agent does everything
- `judge.user.md` — the LLM-as-judge scoring prompt from BENCHMARK.md

The Memory agent's user prompt should explicitly include the `MemoryUpdate` JSON schema so the LLM knows exactly what structure to return.

### Phase 3: Agents

Each agent is an async function: `async def agent_name(state: StoryState, llm: LLMBackend, config: Config, logger: InteractionLogger) -> dict`

Returns a partial state dict that LangGraph merges.

**Narrator** (`narrator.py`):
- Loads and formats `narrator.system.md` and `narrator.user.md` via prompt_loader
- Reads: `user_input`, recent beats, summary, characters, protagonist_location, inventory
- Writes: `current_narration`
- The ONLY output the user sees

**Consistency** (`consistency.py`):
- Returns a `ConsistencyCheck` parsed from the LLM response
- Does NOT rewrite narration — only flags issues

**Director** (`director.py`):
- SILENT — output stored but never shown
- Writes: `scene_description`

**Memory** (`memory.py`):
- Returns a `MemoryUpdate` parsed from the LLM response via the json_sanitizer pipeline
- MERGES updates into existing state — never overwrites unmentioned fields
- Every `summary_interval` turns, also generates a compressed summary

**All agents:**
- Load prompts via `prompt_loader`
- Parse JSON responses through `json_sanitizer` pipeline
- Log every call via `interaction_logger`
- Target ~4-8K tokens per call

### Phase 4: Graphs

**4-agent** (`mas_4_graph.py`):
```
Input → Narrator → Consistency → Director → Memory → Output
                       ↑              │
                       └── (retry) ───┘
```

**3-agent** (`mas_3_graph.py`): Input → Narrator → Consistency → Memory → Output

**2-agent** (`mas_2_graph.py`): Input → Narrator → Memory → Output

**Single LLM** (`single_llm_graph.py`): Input → One agent → Output

Note: LangGraph wants TypedDict for state, but agents work with Pydantic models internally. Bridge this by converting between Pydantic and dict at the graph boundary — `state.model_dump()` to pass in, `StoryState(**state_dict)` to reconstruct. Or use LangGraph's Pydantic state support if available in the installed version.

### Phase 5: Terminal UI

(`src/ui/terminal.py`)

**Single flowing story.** Like reading a book. No panels, no sections.

- Rich for styled text
- Each turn: display narration as continuous flowing text
- Simple input prompt after each narration
- `/status` — world state (characters, location, inventory)
- `/history` — last N beats
- `/quit` — exit
- On startup: scenario setting and protagonist info, or a brief intro for free play

### Phase 6: Benchmark & Evaluation

**Runner** (`src/eval/runner.py`):
Run a scenario through a config. Record per-turn: narration, consistency flags, token usage, latency. Save as JSON. Uses interaction_logger for full LLM call logs.

**Judge** (`src/eval/judge.py`):
Loads `judge.user.md` template. Scores each turn on 4 metrics (1-5). Output per-turn + aggregated.

**Metrics** (`src/eval/metrics.py`):
Character tracking accuracy, contradiction count, latency stats, token efficiency.

**Report** (`src/eval/report.py`):
Summary table, scenario breakdown, degradation curve (matplotlib), cost analysis.

### Phase 7: Entry Point

**`main.py`** with argparse:
- `play` — interactive (optional `--scenario`)
- `benchmark` — run scenarios against a config
- `experiment` — all configs × all scenarios, full report

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
matplotlib>=3.8.0
```

## Guidelines

- **Pydantic everywhere** — state, config, scenario, responses. Not raw dicts.
- **Prompt templates as .md files** — never hardcode prompts as Python strings
- **JSON sanitizer for all LLM JSON parsing** — always go through the pipeline
- **Log every LLM call** — via interaction_logger
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

## Build Order

1. Pydantic models (state, config, scenario, responses)
2. Utility modules (prompt_loader, json_sanitizer, interaction_logger)
3. LLM backends
4. Prompt templates (.md files)
5. Narrator agent alone → verify it works with a manual test
6. Add Memory → verify
7. Add Consistency → verify
8. Add Director → verify
9. Build all 4 graph variants
10. Terminal UI
11. Benchmark pipeline
12. Main entry point

Test each step before moving on.
