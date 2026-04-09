# interactive-mas

A multi-agent system (MAS) for interactive storytelling, built with LangGraph. Specialized agents collaborate to interpret user input and continuously adapt a coherent narrative in real time.

## Overview

Users interact via a terminal, typing natural-language commands to steer a protagonist through a story. The MAS interprets each command, generates the next narrative beat, checks for contradictions, and maintains a persistent world state — all transparently.

The terminal shows a single flowing story. One sentence after another, like reading a book. The agents work behind the scenes.

A benchmark suite compares different MAS configurations (1, 2, 3, and 4 agents) against each other using predefined 50-turn scenarios scored by an LLM-as-judge.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the MAS design, state schema, agent specs, and graph topology.

## Benchmark

See [BENCHMARK.md](BENCHMARK.md) for the evaluation methodology, metrics, and experiment matrix.

## Project Structure

```
interactive-mas/
├── README.md
├── ARCHITECTURE.md
├── BENCHMARK.md
├── CLAUDE.md                        # Claude Code working instructions
├── scenarios/
│   ├── exploration.json
│   ├── mystery.json
│   ├── survival.json
│   ├── social_intrigue.json
│   └── puzzle.json
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── narrator.py
│   │   ├── director.py              # Silent — stored for future video pipeline
│   │   ├── consistency.py
│   │   └── memory.py
│   ├── state/
│   │   ├── __init__.py
│   │   └── story_state.py           # Pydantic models for all state
│   ├── models/
│   │   ├── __init__.py
│   │   ├── config.py                # Pydantic config models loaded from YAML
│   │   ├── scenario.py              # Pydantic scenario models loaded from JSON
│   │   └── responses.py             # Pydantic response schemas for agent outputs
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── mas_4_graph.py
│   │   ├── mas_3_graph.py
│   │   ├── mas_2_graph.py
│   │   └── single_llm_graph.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── gemma.py
│   │   └── openai_backend.py
│   ├── prompts/                     # Prompt templates as .md files
│   │   ├── narrator.system.md
│   │   ├── narrator.user.md
│   │   ├── consistency.system.md
│   │   ├── consistency.user.md
│   │   ├── director.system.md
│   │   ├── director.user.md
│   │   ├── memory.system.md
│   │   ├── memory.user.md
│   │   ├── single_llm.system.md
│   │   ├── single_llm.user.md
│   │   └── judge.user.md
│   ├── util/
│   │   ├── __init__.py
│   │   ├── json_sanitizer.py        # JSON repair, extraction, sanitization
│   │   ├── prompt_loader.py         # Load and format .md prompt templates
│   │   └── interaction_logger.py    # Full LLM call logging per session
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── runner.py
│   │   ├── judge.py
│   │   ├── metrics.py
│   │   └── report.py
│   └── ui/
│       ├── __init__.py
│       └── terminal.py
├── configs/
│   ├── mas_4_agent.yaml
│   ├── mas_3_agent.yaml
│   ├── mas_2_agent.yaml
│   ├── single_llm.yaml
│   └── single_llm_openai.yaml
├── logs/                            # LLM interaction logs per session
├── results/                         # Benchmark output
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
python main.py play --config configs/mas_3_agent.yaml

# Run a predefined scenario
python main.py play --config configs/mas_3_agent.yaml --scenario scenarios/mystery.json

# Benchmark a single config
python main.py benchmark --config configs/mas_3_agent.yaml --scenarios scenarios/

# Full experiment
python main.py experiment --output results/
```

## Tech Stack

- **LangGraph** — multi-agent orchestration with typed shared state
- **Pydantic v2** — validated models for state, config, scenarios, and LLM responses
- **Gemma 4 31B** — served locally via vLLM
- **OpenAI GPT-4o** — optional alternative backend
- **Rich** — terminal UI
- **vLLM** — high-throughput local LLM serving
