# Untitled Project

An experimental interactive video generation system: an AI-generated film that keeps playing in your browser while you type suggestions into it.

An [Artificial Realities](https://github.com/artificial-realities) project. *Untitled Project* is a working title.

> **Work in progress.** It runs end to end, from a typed line to a clip on screen, but it is a prototype: video needs an 80 GB GPU, there is one small bundled story, and several problems are open (see [Status](#status)).

## Overview

```
┌─▶ the viewer types a suggestion (or doesn't)
│     → Director (LLM) writes the next beat: 1-4 clips of action, dialogue and world changes
│     → code checks it against the world model and compiles a video prompt per clip
│     → LTX-2.5 renders each 6-second clip with sound, continuing from the end of the last one
└──── the browser plays the clips back to back
      (meanwhile the Showrunner, a second LLM, condenses the story so far and plans the next scene)
```

- **Story.** Two LLM roles write the film over a typed world model (characters, props, story threads, an event log) that only code changes. The Director writes one beat per call; the Showrunner runs in the background and never holds up the picture. The story keeps moving when nobody types.
- **Suggestions.** A typed line is a suggestion to the storyteller, not a command. The Director takes it up in the story's own way, maybe with a twist, a character who resists, or a consequence that comes later, and reports whether it was absorbed, deferred or declined. A line reaches the screen about two clips after you type it.
- **Video.** [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) runs on a GPU server you host. Each clip is conditioned on the last frames of the one before, so the picture stays continuous; the first clip starts from the story's reference image. All the sound, the characters' lines included, comes from the video model.
- **Without a GPU** the same film plays as text in the browser or the terminal.

Built with Python (asyncio, Pydantic), the OpenAI API, aiohttp with server-sent events and one HTML page for the player, and FastAPI with Hugging Face `diffusers` on the GPU server. Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

The bundled story is deliberately small: a LEGO minifigure in a white void who talks, meets a spaceman when a bit first fails badly, and gets visitors from other LEGO themes. A story is a folder with a `story.json` and a reference image ([stories/README.md](stories/README.md)).

## Demo

There is no hosted version and no published footage yet. Text mode runs on any computer with an OpenAI key (below).

## Requirements

| | Text mode | Video mode, in addition |
|---|---|---|
| Hardware | Any computer | A Linux machine with an 80 GB NVIDIA GPU (tested on H100), yours or rented |
| Software | Python 3.11-3.14 | ffmpeg; on the GPU machine torch 2.9.1 with CUDA 12.8 |
| Models | OpenAI `gpt-5.6-luna` for both roles | LTX-2.5 weights (~72 GB, gated: accept the licence on Hugging Face) |
| Accounts | [OpenAI API key](https://platform.openai.com/api-keys) | Hugging Face token; optionally Vast.ai to rent the GPU |
| Cost | About $0.50 per hour of film in OpenAI calls | GPU time: a rented H100 has cost about $3 per hour |

## Quickstart

Text mode, on Linux or WSL2:

```bash
git clone https://github.com/artificial-realities/untitled-project.git
cd untitled-project
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add your OpenAI API key to .env
python main.py check              # checks the setup without calling OpenAI
python main.py play --config configs/director.yaml --player web
```

Open <http://127.0.0.1:8765/> and type into the box whenever you like. Ctrl+C stops the film; `--segments 20` stops it after 20 clips. Every run writes a transcript and the world model to `logs/`.

Or with Docker (new, and so far only tested in CI): `cp .env.example .env`, add the key, `docker compose up --build`.

## Video

Video needs the LTX-2.5 server (`server/`) running on a GPU machine. On its first start it downloads the weights and compiles for the clip shape, which takes 5-20 minutes; after that each 6-second clip renders in about 3.5 s on an H100. Then run the film with the live config, ideally on the same machine:

```bash
python main.py check --config configs/director.live.yaml     # is the server reachable, ready and warm?
python main.py play --config configs/director.live.yaml --player web
```

Three ways to get the server, all in [docs/self-hosting.md](docs/self-hosting.md#gpu-server-video-mode):

- **Rent one on Vast.ai** with `server/vast_run.sh up | run | pull | down`: the route the project is developed on.
- **Your own GPU machine** (or another provider): `pip install -r server/requirements.txt` and start it; see [server/README.md](server/README.md).
- **Docker** on a GPU machine: `docker compose --profile gpu up ltx-server` (untested).

## Configuration

Keys go in `.env` (from `.env.example`), never in the configs:

| Variable | |
|---|---|
| `OPENAI_API_KEY` | Required. |
| `HF_TOKEN` | On the GPU machine, to download the gated weights. |
| `LTXV_SERVER_URL` | Where the client finds the LTX-2.5 server (default `http://localhost:8000`). |
| `LTX2_API_KEY` | Optional shared key between the client and the server. |
| `VAST_API_KEY` | Only for `server/vast_run.sh`. |
| `LANGFUSE_*` | Optional tracing (sends prompts and outputs to Langfuse). |

Everything else is in the run configs: `configs/director.yaml` (text) and `configs/director.live.yaml` (video), with every field explained in [`src/models/config.py`](src/models/config.py). The full table and the server's settings are in [docs/self-hosting.md](docs/self-hosting.md#configuration).

The player and the GPU server listen on `127.0.0.1` and have no login, so anyone who can reach them can steer the film and spend your credit. Keep them there, or read [exposing it beyond your computer](docs/self-hosting.md#exposing-it-beyond-your-computer) first.

## Architecture

A deterministic engine drives two LLM roles on two clocks: the Director on the critical path (one structured call per beat, validated against the world model, emitted clip by clip with no model call in between, cut short when the viewer speaks), and the Showrunner off it (memory compaction and the next scene card). A prompt compiler is the only code that knows the video model's dialect. The live loop renders each clip just in time, so that it is ready about 4.7 s before it is due on air and a viewer's line reaches the earliest clip that isn't rendering yet.

The orchestration is one mechanism being explored, not the definition of the project. [docs/vision.md](docs/vision.md) says what it is reaching for, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) how it works, and [docs/decisions.md](docs/decisions.md) why it is built this way, with the evidence.

## Status

Working: end-to-end generation from viewer input to a film streaming in the browser, steered while it plays. A 64-clip live film on one H100 played without a stall, every viewer line on screen two clips after it was typed. Open:

- **The Director's latency tail.** A slow call at a beat's end or on a viewer's line can still hold the picture: a 300-clip film had three stalls, all from Director calls of 6-14 s. The current Director (gpt-5.6-luna) has run headless, not yet in a live film with video.
- **Re-seeding resets the look.** Seeding a clip from the reference image reverts anything the story has changed about a character (a cap picked up mid-film), so only the first clip is seeded and every other continues the one before. A new angle has to be a camera move, or a written cut where the story allows one.
- **Continuity against steering.** The sliding window trades one for the other directly (`ltxv_continuity_strength`): high continuity means new input barely lands, low means the picture jumps. A better mechanism is needed, not a better setting.
- **Long films drift.** As props accumulate, the LEGO story's white void turns into a baseplate floor.
- **Unverified on the GPU:** two speakers in one clip, a minifigure's printed face while it speaks, and the server on the diffusers 0.41.0 release (the live runs used a development build).

### Performance

One live film of 64 clips on an H100 SXM, client and server on the box: LTX-2.5 distilled (8 sigmas, unguided), fp8, regional `torch.compile`, 512×288 at 24 fps, 6 s clips with the overlap seam; gpt-5.4-mini as Director.

| | |
|---|---|
| Render per 6 s clip | 3.5 s mean, 3.7 s p95 (0.62 of the clip's length) |
| Stalls (the picture held for the next clip) | none |
| Smallest lead of a ready clip over its air time | 2.3 s |
| Viewer line to its clip on air | 9.6-13.4 s (9 lines) |
| First picture | 13.8 s |
| Seams | no duplicate frames, no luminance dip, browser swap p95 6.7 ms |
| LLM cost | $0.50 per film-hour (gpt-5.6-luna for both roles) |

`scripts/latency_probe.py` checks whether a server setting renders in real time. Every run writes `logs/sessions/<id>/timing.jsonl` (fires, renders, queue margins, stalls, viewer lines, Director calls) next to its world model and transcript.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest        # offline: mock LLM, no key, no network beyond this machine
ruff check .
```

[AGENTS.md](AGENTS.md) has the repository map, the conventions and the rules for coding agents (and people): it's written so that an agent asked to "set this up so I can run it" can do most of the work. Contributions are welcome: see [CONTRIBUTING.md](.github/CONTRIBUTING.md), and [SECURITY.md](.github/SECURITY.md) for reporting vulnerabilities. Generating anything real costs money, so the tests never call a model or a GPU.

## License and credits

The code is [MIT](LICENSE). Created by Simen Bihaug-Frøyland ([@sbfroy](https://github.com/sbfroy)) and published by [Artificial Realities](https://github.com/artificial-realities).

The LTX-2.5 weights by [Lightricks](https://huggingface.co/Lightricks) are not part of this repository and not covered by its licence: they are downloaded from Hugging Face under the LTX-2 Community License Agreement, which has its own conditions (including a revenue threshold for commercial use). The story and the dialogue are written by OpenAI models.

LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse this project.
