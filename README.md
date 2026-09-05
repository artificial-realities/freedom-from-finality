# Artificial Realities

A four-role LLM workflow for interactive storytelling. A beat writer (Tolkien) and shot composer (Spielberg) form the per-turn critical path, a voice-over commentator (Attenborough) runs on the audio clock, and a memory curator (Spock) runs as an async editor behind the turn — orchestrated by a deterministic turn engine that turns user steering between clips into a continuous flowing video story.

In Anthropic's agents-vs-workflows terminology this is a workflow, not an agent system — no tool use, no LLM-directed control flow.

## Overview

The story plays as a flowing sequence of short clips. Each clip's final frame is the next clip's input image, giving the story a continuous visual feel. A voice-over commentator speaks over the clips in the register the blueprint asks for. The user types guidance between clips — but the story keeps flowing even when they don't.

The default story is deliberately minimal: a single LEGO minifigure alone in an infinite white void, doing whatever comes to mind — props come and go, bits get tried, callbacks build up. No protagonist dialogue, no locations, no other characters. It strips away every variable except long-horizon memory.

Four roles collaborate behind the scenes, split by clock:

- **Tolkien** (narrator) — writes what happens in the next clip: a prose narration, the mechanical action, and the outcome. *Per-turn critical path.*
- **Spielberg** (shot composer) — translates the beat into an image-to-video prompt: camera, composition, motion, and an end-frame description that becomes the next clip's starting image. *Per-turn critical path.*
- **Attenborough** (voice-over commentator) — invoked only when silence exceeds a threshold, reads the beats accumulated since his last line and writes a spoken line. Optionally routed through ElevenLabs TTS when audio is enabled. *Audio clock.*
- **Spock** (memory + context curator) — runs as an async background task on a deep state snapshot, joined at the next turn's start. Updates structured world state and rolling narrative memory, hands Tolkien a filtered context brief. *Async editor.*

Video and audio generation are both opt-out. Every agent always produces complete, usable output, so the system behaves as if rendering were live even when clip and TTS generation are skipped at runtime (the default for headless dev).

## Architecture & direction

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design philosophy, state schema, agent specs, turn execution order.
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
│   ├── ab_scenario.json             # A/B evaluation scenario
│   └── legoman.png                  # Reference image for the LEGO minifigure
├── src/
│   ├── agents/                      # tolkien, spielberg, attenborough, spock
│   ├── state/                       # StoryState and related Pydantic models
│   ├── models/                      # config, story, response schemas
│   ├── graph/                       # TurnEngine (plain asyncio, clock-split orchestration)
│   ├── i2v/                         # image-to-video backends + frame extraction
│   ├── llm/                         # OpenAI + Anthropic backends
│   ├── memory/                      # Mem0 episodic memory store (opt-in)
│   ├── tts/                         # ElevenLabs adapter (only when audio_enabled)
│   ├── playback/                    # clip muxing
│   ├── prompts/                     # one system + user template per agent (+ judge rubrics)
│   ├── eval/                        # scenario runner + reusable LLM-as-judge scorer
│   ├── ui/                          # terminal + Tk popup
│   └── util/                        # json sanitizer, langfuse, media, logging
├── configs/
│   ├── mas.yaml                     # headless dev (video + audio off)
│   ├── mas.live-text.yaml           # Tk popup, no video
│   ├── mas.live.yaml                # full live (DashScope i2v + TTS)
│   ├── mas.live-ltxv.yaml           # full live with LTXV 13b video
│   └── mas.mem0.yaml                # Mem0 episodic memory mode
├── server/
│   └── ltxv_server.py               # resident LTXV inference server (runs on GPU box)
├── plans/                           # numbered evolution plans + workflow README
├── requirements.txt
└── main.py
```

## Quick Start

### Prerequisites

- Python 3.10+
- OpenAI API key — every config calls the OpenAI API
- (Optional) LTXV server on a GPU box — for live video with identity-holding LTXV 13b (see below)
- (Optional) DashScope API key for live video (Alibaba Wan2.x i2v) — legacy backend
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

# Live demo with DashScope video + audio (legacy)
python main.py play --config configs/mas.live.yaml

# Live demo with LTXV 13b video (needs a running LTXV server — see below)
python main.py play --config configs/mas.live-ltxv.yaml
```

### LTXV video server

The LTXV backend runs a resident inference server on a GPU box (≥80 GB VRAM for the 13b model). The server holds the pipeline in memory and manages a sliding-window + reference-anchor loop that holds the subject's identity across turns.

```bash
# On the GPU box:
git clone https://github.com/Lightricks/LTX-Video.git
cd LTX-Video && pip install -e .
pip install fastapi uvicorn av

# Copy the server script into the LTX-Video repo (or set LTXV_REPO_PATH):
cp server/ltxv_server.py .
LTXV_CONFIG=configs/ltxv-13b-0.9.8-distilled.yaml python ltxv_server.py
# → Pipeline loaded in ~15s, listening on :8000

# On your machine — point the config at the server:
#   Edit configs/mas.live-ltxv.yaml → ltxv_server_url: "http://<gpu-box-ip>:8000"
python main.py play --config configs/mas.live-ltxv.yaml
```

## Tech Stack

- **Plain asyncio** — deterministic turn engine with clock-split orchestration (Tolkien→Spielberg critical path, Attenborough on audio clock, Spock async)
- **Pydantic v2** — validated models for state, config, story, and all LLM response schemas
- **OpenAI** — backbone model for every agent (configurable per run)
- **ElevenLabs** — optional TTS for Attenborough's commentary
