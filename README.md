# Artificial Realities

A four-role LLM workflow for interactive storytelling. A beat writer (**Tolkien**) and shot composer (**Spielberg**) form the per-turn critical path, a voice-over commentator (**Attenborough**) runs on the audio clock, and a memory curator (**Spock**) runs as an async editor behind the turn — orchestrated by a deterministic turn engine that turns user steering between clips into a continuous flowing video story.

This is a workflow, not an agent system — no tool use, no LLM-directed control flow.

## How it works

The story plays as a flowing sequence of short video clips. Each clip's visual state carries forward through a sliding-window continuity scheme on the LTX-2.5 server, giving the story a continuous visual feel. A voice-over commentator speaks over the clips when silence has built up. The user types guidance between clips — but the story keeps flowing even when they don't.

The default story is a single LEGO minifigure alone in an infinite white void, doing whatever comes to mind. No protagonist dialogue, no locations, no other characters. It strips away every variable except long-horizon memory.

Four roles collaborate behind the scenes, split by clock:

- **Tolkien** (narrator) — writes what happens next: prose narration, action, and outcome. *Per-turn critical path.*
- **Spielberg** (shot composer) — translates the beat into an image-to-video prompt: camera, composition, motion, and an end-frame description. *Per-turn critical path.*
- **Attenborough** (voice-over) — invoked only when silence exceeds a threshold, reads the accumulated beats and writes a spoken line. Optionally routed through ElevenLabs TTS. *Audio clock.*
- **Spock** (memory curator) — runs as an async background task, updates structured world state and rolling narrative memory, hands Tolkien a filtered context brief. *Async editor.*

Video and audio generation are both opt-out. Every agent always produces complete output, so the system behaves as if rendering were live even when video/TTS are disabled (the default for headless dev).

## Architecture

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design philosophy, state schema, agent specs, turn execution order.
- [docs/AGENT_FLOW.md](docs/AGENT_FLOW.md) — per-agent walkthrough of the pipeline.

## Project structure

```
ArtificialRealities/
├── main.py                              # Entry point (play command)
├── CLAUDE.md                            # Claude Code working instructions
├── docs/
│   ├── ARCHITECTURE.md                  # Design, agent specs, state schema, turn order
│   └── AGENT_FLOW.md                    # Per-agent pipeline walkthrough
├── data/
│   ├── story.json                       # Story blueprint
│   ├── demo_scenario.json               # Scripted demo input
│   └── legoman.png                      # Seed image
├── src/
│   ├── agents/                          # tolkien, spielberg, attenborough, spock
│   ├── state/                           # StoryState and related Pydantic models
│   ├── models/                          # config, story, response schemas
│   ├── graph/                           # TurnEngine (asyncio, clock-split orchestration)
│   ├── i2v/                             # LTX-2.5 image-to-video backend
│   ├── llm/                             # OpenAI backend
│   ├── memory/                          # Mem0 episodic memory store (opt-in)
│   ├── tts/                             # ElevenLabs adapter
│   ├── playback/                        # clip muxing (ffmpeg)
│   ├── prompts/                         # system + user template per agent
│   ├── eval/                            # scenario runner, interactive + live loops
│   ├── ui/                              # terminal input
│   └── util/                            # json sanitizer, langfuse, media, logging
├── configs/
│   ├── mas.yaml                         # headless dev (video + audio off)
│   ├── mas.live.yaml                    # live with LTX-2.5 video
│   └── mas.mem0.yaml                    # opt-in Mem0 episodic memory
├── server/
│   ├── ltx2_server.py                   # Resident LTX-2.5 inference server (GPU box)
│   └── requirements.txt                 # GPU-box deps
└── requirements.txt                     # Local deps
```

## Quick start

### Prerequisites

- Python 3.10+
- OpenAI API key
- (Optional) LTX-2.5 server on a GPU box — for live video (see below)
- (Optional) ElevenLabs API key — for spoken voice-over
- (Optional) `ffmpeg` on PATH — for muxing TTS audio onto clips

### Installation

```bash
git clone <repo-url>
cd ArtificialRealities
pip install -r requirements.txt
cp .env.example .env   # fill in the keys you need
```

### Running

```bash
# Headless dev — sequential terminal loop, no video or audio
python main.py play --config configs/mas.yaml

# Scripted scenario — non-interactive
python main.py play --config configs/mas.yaml --scenario data/demo_scenario.json

# Live with LTX-2.5 video (needs a running LTX-2.5 server)
python main.py play --config configs/mas.live.yaml
```

### LTX-2.5 video server

The video backend runs a resident inference server on a GPU box. It holds the pipeline in memory and owns the sliding window: it keeps the tail of each generated chunk and conditions the next one on it, so the client only ever sends a prompt. LTX-2.5 generates synchronized audio alongside the video, so clips arrive with an ambient/SFX track already on them.

**The weights are gated.** One-time setup:

1. Open [Lightricks/LTX-2.5-Diffusers](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) and click **Agree and access repository**.
2. Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

```bash
# On the GPU box:
pip install -r server/requirements.txt
export HF_TOKEN=hf_...
python ltx2_server.py          # downloads weights on first run, serves on :8000

# Lower VRAM usage:
LTX2_OFFLOAD=cpu python ltx2_server.py     # CPU offload between calls
LTX2_VAE_TILING=1 python ltx2_server.py    # lower peak VRAM on decode

# On your machine — tunnel and run:
ssh -N -L 8000:localhost:8000 -p <PORT> root@<HOST>
python main.py play --config configs/mas.live.yaml
```

The server runs the **base model** at 15 sampling steps — the quality/speed sweet spot tested on A100-80GB (~170s/clip with CPU offload). The distilled LoRA (`LTX2_DISTILLED=1`) is currently broken in diffusers ≤0.41 (missing connector modules cause corrupted output); leave it off until a future diffusers release wires those up.

`ltxv_continuity_strength` in the config controls the continuity-vs-steering trade-off. At `1.0` continuity is maximal but new prompts barely land; lower values let steering through at the cost of visual continuity.

## Tech stack

- **Plain asyncio** — deterministic turn engine with clock-split orchestration
- **Pydantic v2** — validated models for state, config, story, and all LLM responses
- **OpenAI** — backbone model for every agent (configurable per run)
- **LTX-2.5** — 22B image-to-video model with native audio generation
- **ElevenLabs** — optional TTS for Attenborough's voice-over
