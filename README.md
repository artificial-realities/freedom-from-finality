# Artificial Realities

A four-role workflow for interactive storytelling, built with LangGraph. Four specialized LLMs — a beat writer, a shot composer, a voice-over commentator, and a memory curator — coordinate over a fixed, deterministic pipeline to turn the user's steering between clips into a continuous flowing video story: short clips chained via image-to-video (i2v), layered with live voice-over commentary, while the story keeps moving even when the user is silent.

In Anthropic's agents-vs-workflows terminology this is a workflow, not an agent system — no tool use, no LLM-directed control flow.

## Overview

The story plays as a flowing sequence of ~5-second clips. Each clip's final frame is the next clip's input image, giving the story a continuous visual feel. A voice-over commentator speaks over the clips in the register the blueprint asks for. The user types guidance between clips — but the story keeps flowing even when they don't.

The default story is deliberately minimal: a single LEGO minifigure alone in an infinite white void, doing whatever comes to mind — props come and go, bits get tried, callbacks build up. No protagonist dialogue, no locations, no other characters. It strips away every variable except long-horizon memory.

Four agents collaborate behind the scenes:

- **Tolkien** (narrator) — writes what happens in the next clip: a prose narration, the mechanical action, and the outcome.
- **Spielberg** (shot composer) — translates the beat into an image-to-video prompt: camera, composition, motion, and an end-frame description that becomes the next clip's starting image.
- **Attenborough** (voice-over commentator) — reads Tolkien's beat and Spielberg's shot and writes the spoken line that plays over the clip. Optionally routed through ElevenLabs TTS when audio is enabled; otherwise logged as text.
- **Spock** (memory + context curator) — after each turn, updates structured world state and a rolling narrative memory, and hands Tolkien a filtered context brief — including the currently-relevant characters and locations — for the next turn.

Video and audio generation are both opt-out. Every agent always produces complete, usable output, so the system behaves as if rendering were live even when clip and TTS generation are skipped at runtime (the default for headless dev).

## Architecture & direction

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design philosophy, state schema, agent specs, turn execution order, pipeline buffer, and the one-turn-delayed feedback loop.
- [docs/AGENT_FLOW.md](docs/AGENT_FLOW.md) — a hands-on per-agent walkthrough of the pipeline.
- [docs/NORTH_STAR.md](docs/NORTH_STAR.md) — where the system is heading.
- [docs/EVOLUTION.md](docs/EVOLUTION.md) + [plans/](plans/) — the roadmap and the per-topic plans that drive it.
- [docs/findings/](docs/findings/) — context preserved from the project's earlier benchmark phase, for reference.

## Project Structure

```
ArtificialRealities/
├── README.md
├── CLAUDE.md                        # Claude Code working instructions
├── docs/
│   ├── ARCHITECTURE.md              # Design philosophy, agent specs, state schema, turn order
│   ├── AGENT_FLOW.md                # Per-agent pipeline walkthrough
│   ├── NORTH_STAR.md                # Technical direction
│   ├── EVOLUTION.md                 # Roadmap quarry
│   └── findings/                    # Preserved context from the earlier phase
├── data/
│   ├── story.json                   # Story blueprint
│   ├── demo_scenario.json           # Canonical scripted demo input
│   └── legoman.png                  # Reference image for the LEGO minifigure
├── src/
│   ├── agents/                      # tolkien, spielberg, attenborough, spock
│   ├── state/                       # StoryState and related Pydantic models
│   ├── models/                      # config, story, response schemas
│   ├── graph/                       # mas_graph (Tolkien → Spielberg → Attenborough → Spock)
│   ├── i2v/                         # image-to-video backends + frame extraction
│   ├── llm/                         # OpenAI + Anthropic backends
│   ├── tts/                         # ElevenLabs adapter (only when audio_enabled)
│   ├── playback/                    # clip muxing + player
│   ├── prompts/                     # one system + user template per agent (+ judge rubrics)
│   ├── eval/                        # scenario runner + reusable LLM-as-judge scorer
│   ├── ui/                          # terminal + Tk popup
│   └── util/                        # json sanitizer, langfuse, media, logging
├── configs/
│   ├── mas.yaml                     # headless dev (video + audio off)
│   ├── mas.live-text.yaml           # Tk popup, no video
│   └── mas.live.yaml                # full live (i2v + TTS)
├── plans/                           # numbered evolution plans + workflow README
├── requirements.txt
└── main.py
```

## Quick Start

### Prerequisites

- Python 3.10+
- OpenAI API key — every config calls the OpenAI API
- (Optional) DashScope API key for live video (Alibaba Wan2.x i2v) — only when `video_enabled: true`
- (Optional) ElevenLabs API key for live voice-over — skipped by default
- (Optional) `ffmpeg` on PATH for muxing TTS audio onto video clips — fails soft if missing

### Installation

```bash
git clone <repo-url>
cd ArtificialRealities
pip install -r requirements.txt
cp .env.example .env   # fill in only the keys you actually need
```

### Running

```bash
# Headless dev: sequential terminal loop, video + audio disabled
python main.py play --config configs/mas.yaml

# Drive a scripted scenario non-interactively
python main.py play --config configs/mas.yaml --scenario data/demo_scenario.json

# Live demo, no video — story unfolds in a Tk popup; type guidance in the terminal
python main.py play --config configs/mas.live-text.yaml

# Live demo with video + audio: needs DASHSCOPE_API_KEY (and ELEVENLABS_API_KEY)
python main.py play --config configs/mas.live.yaml
```

## Tech Stack

- **LangGraph** — multi-agent orchestration with typed shared state
- **Pydantic v2** — validated models for state, config, story, and all LLM response schemas
- **OpenAI** — backbone model for every agent (configurable per run)
- **ElevenLabs** — optional TTS for Attenborough's commentary
