# interactive-mas

A multi-agent system (MAS) for interactive storytelling, built with LangGraph. Specialized agents collaborate to interpret user input and continuously adapt a coherent narrative in real time.

## Overview

Users interact via a terminal, typing natural-language commands to steer a protagonist through a story. The MAS interprets each command, generates the next narrative beat, checks for contradictions, and maintains a persistent world state — all transparently.

The terminal shows a single flowing story. One sentence after another, like reading a book. The agents work behind the scenes.

A benchmark compares different agent configurations against each other using a predefined 100-turn scenario. The full session logs are evaluated post-hoc — no automated scoring pipeline, just structured logs that can be reviewed by a human or handed to an LLM for analysis.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the MAS design, state schema, agent specs, and graph topology.

## Benchmark

See [BENCHMARK.md](BENCHMARK.md) for the evaluation methodology and experiment matrix.

## Project Structure

```
interactive-mas/
├── README.md
├── ARCHITECTURE.md
├── BENCHMARK.md
├── CLAUDE.md                        # Claude Code working instructions
├── story.json                       # Story blueprint (setting, protagonist, rules, premise)
├── test_scenario.json               # 100-turn benchmark scenario
├── reference/                       # Reference implementations to adapt
│   ├── json_sanitizer.py
│   └── interaction_logger.py
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── narrator.py              # "Tolkien" — the storyteller (respects world_constraints)
│   │   ├── editor.py                # "Wilde" — polishes tone
│   │   ├── memory.py                # "Sheldon" — structured world state tracker
│   │   └── threads.py               # "Chekhov" — narrative thread tracker
│   ├── state/
│   │   ├── __init__.py
│   │   └── story_state.py           # Pydantic models for all state
│   ├── models/
│   │   ├── __init__.py
│   │   ├── config.py                # Pydantic config models loaded from YAML
│   │   ├── story.py                 # Pydantic story blueprint model loaded from JSON
│   │   └── responses.py             # Pydantic response schemas (MemoryUpdate, ThreadUpdate)
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── solo_graph.py            # Single LLM
│   │   ├── core_graph.py            # Tolkien → Sheldon
│   │   └── full_cast_graph.py       # Tolkien → Wilde → [Sheldon ∥ Chekhov]
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── gemma.py
│   │   └── openai_backend.py
│   ├── prompts/                     # Prompt templates as .md files
│   │   ├── narrator.system.md
│   │   ├── narrator.user.md
│   │   ├── editor.system.md
│   │   ├── editor.user.md
│   │   ├── memory.system.md
│   │   ├── memory.user.md
│   │   ├── threads.system.md
│   │   ├── threads.user.md
│   │   ├── single_llm.system.md
│   │   └── single_llm.user.md
│   ├── util/
│   │   ├── __init__.py
│   │   ├── json_sanitizer.py        # JSON repair, extraction, sanitization
│   │   ├── prompt_loader.py         # Load and format .md prompt templates
│   │   └── interaction_logger.py    # Full LLM call logging per session
│   ├── eval/
│   │   ├── __init__.py
│   │   └── runner.py                # Runs the scenario, logs everything
│   └── ui/
│       ├── __init__.py
│       └── terminal.py
├── configs/
│   ├── solo.yaml
│   ├── core.yaml
│   └── full_cast.yaml
├── logs/                            # LLM interaction logs per session
├── requirements.txt
└── main.py
```

## Quick Start

### Prerequisites

- Python 3.10+
- GPU with sufficient VRAM for Gemma 4 31B (H100 recommended)
- (Optional) OpenAI API key for GPT-4o comparison

### Installation

```bash
git clone <repo-url>
cd interactive-mas
pip install -r requirements.txt
```

### Serving Gemma 4 locally

Start vLLM before running the project:

```bash
vllm serve google/gemma-4-31b-it \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --port 8000
```

### Running

```bash
# Interactive play
python main.py play --config configs/full_cast.yaml

# Run the benchmark scenario
python main.py play --config configs/core.yaml --scenario test_scenario.json

# Benchmark all configs against the scenario
python main.py benchmark --scenario test_scenario.json
```

## Tech Stack

- **LangGraph** — multi-agent orchestration with typed shared state
- **Pydantic v2** — validated models for state, config, story, and LLM responses
- **Gemma 4 31B** — served locally via vLLM
- **OpenAI GPT-4o** — optional alternative backend
- **Rich** — terminal UI
- **vLLM** — high-throughput local LLM serving
