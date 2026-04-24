# ClankerStudios

A four-role workflow for interactive storytelling, built with LangGraph. Four specialized LLMs — a beat writer, a shot composer, a voice-over commentator, and a memory curator — coordinate over a fixed, deterministic pipeline to produce a continuous stream of short video clips chained via image-to-video (i2v), layered with live voice-over commentary, while the user optionally steers the story with natural-language commands between clips.

In Anthropic's agents-vs-workflows terminology this is a workflow, not an agent system — no tool use, no LLM-directed control flow. We also use "MAS" as academic shorthand for the same four-role decomposition.

## Overview

The story plays as a flowing sequence of ~5-second clips. Each clip's final frame is the next clip's input image, giving the story a continuous visual feel. A voice-over commentator speaks over the clips in the register the blueprint asks for. The user types guidance between clips — but the story keeps flowing even when they don't.

The test story is deliberately minimal: a single LEGO minifigure alone in an infinite white void, doing whatever comes to mind — props come and go, bits get tried, callbacks build up. No protagonist dialogue, no locations, no other characters. The setting strips away every variable except long-horizon memory, which is exactly the dimension the benchmark is built to test.

Four agents collaborate behind the scenes:

- **Tolkien** (narrator) — writes what happens in the next clip: a prose narration, the mechanical action, and the outcome.
- **Spielberg** (shot composer) — translates the beat into an image-to-video prompt: camera, composition, motion, and an end-frame description that becomes the next clip's starting image.
- **Attenborough** (voice-over commentator) — reads Tolkien's beat and Spielberg's shot and writes the spoken line that plays over the clip. Optionally routed through ElevenLabs TTS when audio is enabled; otherwise logged as text.
- **Spock** (memory + context curator) — after each turn, updates structured world state and a rolling narrative memory, and hands Tolkien a filtered context brief — including the currently-relevant characters and locations — for the next turn.

A benchmark compares this MAS against a single well-briefed LLM baseline using a predefined 100-turn scenario. Both configurations do the same work — produce a beat, a shot, commentary, and a memory update each turn; the MAS just splits the responsibility across four agents. Full session logs are evaluated post-hoc — no automated scoring, just structured logs that a human or an LLM can review.

Video and audio generation are both opt-out. The system is designed so the MAS always behaves as if rendering were live — every agent produces complete, usable output — but actual clip and TTS generation can be skipped at runtime, which is the default for benchmark runs.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the MAS design, state schema, agent specs, turn execution order, pipeline buffer, and the one-turn-delayed feedback loop.

## Benchmark

See [docs/BENCHMARK.md](docs/BENCHMARK.md) for the research question, experiment matrix, and evaluation methodology.

## Project Structure

```
ClankerStudios/
├── README.md
├── CLAUDE.md                        # Claude Code working instructions
├── docs/
│   ├── ARCHITECTURE.md              # Design philosophy, agent specs, state schema, turn order
│   └── BENCHMARK.md                 # Research question, experiment matrix, evaluation
├── data/
│   ├── story.json                   # Story blueprint (visual_style, tone_guidelines, locations, characters, rules, premise, directions)
│   ├── test_scenario.json           # 100-turn benchmark scenario
│   └── legoman.png                  # Reference image for the LEGO minifigure
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── tolkien.py               # Beat writer; updates narrative direction
│   │   ├── spielberg.py             # Shot composer; i2v prompts
│   │   ├── attenborough.py          # Voice-over commentary; pacing + spans
│   │   ├── spock.py                 # world_state, narrative_memory, context_brief
│   │   └── solo.py                  # Monolithic baseline emitting all four shapes
│   ├── state/
│   │   ├── __init__.py
│   │   └── story_state.py           # StoryState and related Pydantic models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── config.py                # Config model loaded from YAML
│   │   ├── story.py                 # Story blueprint model loaded from JSON
│   │   └── responses.py             # Beat, Shot, Commentary, MemoryUpdate, WorldStateDelta
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── solo_graph.py            # Single LLM (fully briefed, emits all four shapes)
│   │   └── mas_graph.py             # Tolkien → Spielberg → Attenborough → Spock
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── gemma.py
│   │   └── openai_backend.py
│   ├── tts/
│   │   ├── __init__.py
│   │   └── elevenlabs.py            # ElevenLabs TTS adapter — called only when audio_enabled: true
│   ├── prompts/                     # One system + user template per agent
│   │   ├── tolkien.system.md
│   │   ├── tolkien.user.md
│   │   ├── spielberg.system.md
│   │   ├── spielberg.user.md
│   │   ├── attenborough.system.md
│   │   ├── attenborough.user.md
│   │   ├── spock.system.md
│   │   ├── spock.user.md
│   │   ├── solo.system.md
│   │   └── solo.user.md
│   ├── util/
│   │   ├── __init__.py
│   │   ├── json_sanitizer.py
│   │   ├── prompt_loader.py
│   │   └── interaction_logger.py
│   ├── eval/
│   │   ├── __init__.py
│   │   └── runner.py                # Scenario runner, logs every LLM call
│   └── ui/
│       ├── __init__.py
│       └── terminal.py
├── configs/
│   ├── solo.yaml
│   └── mas.yaml
├── logs/                            # LLM interaction logs per session (and logs/audio/ when audio_enabled)
├── requirements.txt
└── main.py
```

## Quick Start

### Prerequisites

- Python 3.10+
- GPU with sufficient VRAM for Gemma 4 31B (H100 recommended) when running locally
- (Optional) OpenAI API key for GPT-4o comparison
- (Optional) DashScope API key for live video (Alibaba Wan2.x i2v) — only when `video_enabled: true`
- (Optional) ElevenLabs API key for live voice-over — skipped by default
- (Optional) `ffmpeg` on PATH for muxing TTS audio onto video clips — fails soft if missing

### Installation

```bash
git clone <repo-url>
cd ClankerStudios
pip install -r requirements.txt
cp .env.example .env   # fill in only the keys you actually need
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
# Interactive play (MAS, video + audio disabled)
python main.py play --config configs/mas.yaml

# Run the benchmark scenario against one config
python main.py play --config configs/solo.yaml --scenario data/test_scenario.json

# Benchmark both configs against the scenario
python main.py benchmark --scenario data/test_scenario.json
```

## Tech Stack

- **LangGraph** — multi-agent orchestration with typed shared state
- **Pydantic v2** — validated models for state, config, story, and all LLM response schemas
- **Gemma 4 31B** — served locally via vLLM (OpenAI-compatible endpoint)
- **OpenAI GPT-4o** — optional alternative backend
- **ElevenLabs** — optional TTS for Attenborough's commentary
- **Rich** — terminal UI
- **vLLM** — high-throughput local LLM serving
