# Running Untitled Project yourself

Untitled Project runs on your own machines, for you and the people at your keyboard. This page covers installation, configuration, costs, the GPU server for video, Docker, and troubleshooting. The [README](../README.md) has the short version.

- [Two modes](#two-modes)
- [Requirements](#requirements)
- [Install and run (text mode)](#install-and-run-text-mode)
- [Run with Docker](#run-with-docker)
- [Configuration](#configuration)
- [Models and costs](#models-and-costs)
- [GPU server (video mode)](#gpu-server-video-mode)
- [What is stored on disk](#what-is-stored-on-disk)
- [Exposing it beyond your computer](#exposing-it-beyond-your-computer)
- [Troubleshooting](#troubleshooting)

## Two modes

| | Text mode | Video mode |
|---|---|---|
| Config | `configs/director.yaml` | `configs/director.live.yaml` |
| What you see | The film as text at story pace: each clip's action and lines, in the browser or the terminal | The film as 6-second video clips with sound, played back to back in the browser |
| Needs | Python and an OpenAI API key | The same, plus an LTX-2.5 server on an 80 GB NVIDIA GPU and ffmpeg |

Both modes run the same story engine: the Director and Showrunner write the same beats, lines and world changes whether anything renders or not. Text mode is the way to try the storytelling, develop, and check a setup before paying for a GPU.

## Requirements

- **Python 3.11-3.14.** The test suite passes on 3.11, 3.13 and 3.14 (CI runs 3.11 and 3.14). `main.py` refuses to start below 3.10.
- **An OpenAI API key** from <https://platform.openai.com/api-keys>, on an account with API credit and access to the configured models ([Models and costs](#models-and-costs)).
- **Linux.** The project is developed on Linux (including WSL2). macOS should work for text mode but hasn't been tested; native Windows hasn't been tested either (use WSL2).
- **ffmpeg** (`ffmpeg` and `ffprobe` on the PATH) for video mode, on the client and on the GPU server. `ffplay` only for the terminal player. Text mode doesn't need it.
- For video mode, **a GPU server**: see [GPU server](#gpu-server-video-mode).

## Install and run (text mode)

```bash
git clone https://github.com/artificial-realities/untitled-project.git
cd untitled-project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then open .env and add your OpenAI API key
python main.py check        # validates the setup; never calls OpenAI
python main.py play --config configs/director.yaml --player web
```

Open <http://127.0.0.1:8765/>. The film starts on its own; type a suggestion into the box at any moment. Ctrl+C in the terminal stops it, and `--segments 20` stops after 20 clips. Without `--player web` the film runs turn by turn in the terminal, asking for input between clips.

`python main.py check --config <config>` reports what a config needs and what is missing, without paid calls: the Python version, whether `OPENAI_API_KEY` is set (not whether OpenAI accepts it), the story, and for the live config ffmpeg and the LTX-2.5 server. It exits 1 when something is missing. `play` runs the same checks before it starts.

To run the tests too: `pip install -r requirements-dev.txt`, then `python -m pytest` (offline, no key needed).

## Run with Docker

> The Docker files are new. CI builds the client image and checks that it starts, but neither image has been run by the maintainers yet.

```bash
cp .env.example .env    # then add your OpenAI API key to .env
docker compose up --build
```

Open <http://localhost:8765/>. Stop it with Ctrl+C, or `docker compose down`.

- The keys are read from `.env` when the container starts. They are never copied into the image (`.dockerignore` excludes `.env`).
- The port is published on `127.0.0.1` only, so other machines can't reach it. Inside the container the player listens on `0.0.0.0`, which is what makes the published port work (it prints a note saying so).
- Run output goes to the `logs` volume (`docker compose cp app:/app/logs ./logs-from-docker` copies it out while the container exists).
- `PLAY_CONFIG` in `.env` picks the config (default `configs/director.yaml`). For video, set `PLAY_CONFIG=configs/director.live.yaml` and point `LTXV_SERVER_URL` at the server: `http://host.docker.internal:8000` for a server or ssh tunnel on the host, or see [the server in Docker](#c-docker-on-a-gpu-machine).

Without Compose:

```bash
docker build -t untitled-project .
docker run --rm --env-file .env -p 127.0.0.1:8765:8765 untitled-project
```

## Configuration

Secrets and machine-specific settings are environment variables, usually set in `.env` in the project root (copy `.env.example`). Real environment variables take precedence over `.env`.

| Variable | Needed for | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | **every config** | The Director and Showrunner calls. Create one at <https://platform.openai.com/api-keys>; usage is billed to its account. |
| `HF_TOKEN` | the GPU server | Downloads the gated LTX-2.5 weights. Accept the licence at <https://huggingface.co/Lightricks/LTX-2.5-Diffusers>, then create a read token at <https://huggingface.co/settings/tokens>. |
| `LTXV_SERVER_URL` | video, optional | Where the client finds the LTX-2.5 server; overrides the config's `ltxv_server_url` (`http://localhost:8000`). |
| `LTX2_API_KEY` | optional | A shared key. On the server, `/generate` and `/reset` then need it; the client (and `scripts/latency_probe.py`) send the same value. Use letters and digits. |
| `VAST_API_KEY` | `server/vast_run.sh` only | Rents GPUs on Vast.ai: <https://cloud.vast.ai/manage-keys/>. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | optional | Tracing. With a public key set, every LLM call's prompts and outputs are sent to Langfuse. |
| `PLAY_CONFIG` | Docker Compose only | The config `docker compose up` plays. |

The OpenAI Python SDK also honours its own standard variables, such as `OPENAI_BASE_URL` and `OPENAI_ORG_ID`. Only the OpenAI API itself has been tested.

The GPU server reads its own `LTX2_*` settings (resolution, quantisation, compilation, attention, warm-up, bind address); they are listed in the docstring of [`server/ltx2_server.py`](../server/ltx2_server.py), and [`server/server_defaults.sh`](../server/server_defaults.sh) holds the defaults the scripts and the server image start with.

Everything about a run that isn't a secret lives in the config YAML: the models, the clip length, how far ahead clips render, how often the Showrunner runs, the LTX-2.5 continuity settings. Every field is commented in [`src/models/config.py`](../src/models/config.py); the main ones are in [ARCHITECTURE.md](ARCHITECTURE.md#configuration). Copy a config to change it. The story is a folder under `stories/` with a `story.json` and a reference image (`--story stories/<folder>`, default `stories/lego-void`); [stories/README.md](../stories/README.md) is the field guide.

## Models and costs

**Language models.** Both roles use OpenAI's `gpt-5.6-luna` with reasoning effort `none` (`director_model`, `showrunner_model`, `*_reasoning_effort` in the configs). The Director makes one call per beat (one to four 6-second clips) and one more for each viewer line; the Showrunner runs every ten clips or so and at scene ends. Together they have cost about **$0.50 per hour of film**. The calls use OpenAI's strict structured outputs; another model has to support them. For a chat model without reasoning effort (such as `gpt-4.1`), set `*_reasoning_effort: ""` in your copy of the config. Prices change: check <https://openai.com/api/pricing/> and consider a monthly budget limit on the OpenAI account.

**Video model.** LTX-2.5 by Lightricks, run on your GPU server from [`Lightricks/LTX-2.5-Diffusers`](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) with Hugging Face `diffusers`. No video API is billed; the cost is the GPU. Renting an H100 on Vast.ai cost about $3 per hour in September 2026 (an H100 NVL at $3.07/h), and each GPU session during development, setup included, cost about $3-4.

**The weights' licence.** The LTX-2.5 weights are not part of this repository and are not covered by its MIT licence. They are gated on Hugging Face under the LTX-2 Community License Agreement: you accept it (and share your contact details with Lightricks) to download them. As summarised on the model page, it allows free commercial use for entities under $10M annual revenue and requires a paid agreement above that, and the licence's attachment restricts how the model and its output may be used. Read the full terms on the model page before you use them. The pipeline's text encoder (Gemma-based) and other components may carry their own terms; the model card lists them.

## GPU server (video mode)

The video comes from `server/ltx2_server.py`, a FastAPI server that keeps the LTX-2.5 pipeline resident in GPU memory and owns the sliding window (the tail of each clip conditions the next). The client sends a compiled prompt per clip and gets back an MP4 with sound. Details of the server, its settings and its recipe are in [server/README.md](../server/README.md).

### What it needs

| | |
|---|---|
| GPU | NVIDIA with **80 GB** of memory. Tested: H100 SXM 80 GB (the performance numbers in the README), H100 NVL; a B200 has run it with the default image. fp8 matmuls (`LTX2_QUANT=fp8`, the default) need Hopper or newer; other cards are untested. |
| Memory use | About 72 GB resident (DiT 40 GB, Gemma text encoder 24 GB, connectors 6 GB, decoders 2 GB) plus activations. Offloading (`LTX2_OFFLOAD=text` or `cpu`) lowers it but makes each render about 2× (`text`) or 4× (`cpu`) slower, past real time, so the film stalls. Smaller GPUs are untested. |
| Disk | About 72 GB of weights for the default (distilled) variant, more for `LTX2_VARIANT=full`. `vast_run.sh` rents 200 GB. |
| Software | Linux, an NVIDIA driver for CUDA 12.8, torch 2.9.1 (the `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel` image is the tested environment), ffmpeg, `server/requirements.txt` (diffusers 0.41.0). |
| Network | The client reaches the server over HTTP (port 8000). Each clip's MP4 comes back over that connection, so **run the client on the GPU machine** if you can: through an ssh tunnel over the internet a clip cost 1.5-4.6 s more, enough to make the film stall. |

**Startup.** On its first start the server downloads the weights (tens of GB; `HF_HUB_ENABLE_HF_TRANSFER=1` speeds it up), loads the pipeline, then renders throwaway clips to compile the model for the run's one clip shape (90-170 s per frame count under fp8). Expect 5-20 minutes on a fresh rented box, a few minutes on later starts with the weights cached. `GET /health` says `loading`, then `warming`, then `ready`; `/generate` answers 503 until it is ready, and `python main.py check --config configs/director.live.yaml` says what it is waiting for. The client refuses to start until the server reports the config's clip shape as warmed (`warm_frames`; `python -m src.i2v.shape <config>` prints the list the server needs as `LTX2_WARMUP_FRAMES`, and `server_defaults.sh` defaults to the live config's).

There are three routes, from the most used to the least.

### A. Rent a GPU on Vast.ai (`server/vast_run.sh`)

The route the project has been developed and measured on. The script rents the cheapest matching H100 offer, installs the server's dependencies on the provider's PyTorch image, starts the server, tunnels it to your `localhost:8000`, ships your working tree to the box and runs the film there, and destroys the instance.

Needs: `VAST_API_KEY`, `HF_TOKEN` and `OPENAI_API_KEY` in `.env`; the `vastai` CLI (`pip install vastai`); credit on the Vast.ai account; your ssh public key registered on Vast (`vastai create ssh-key "$(cat ~/.ssh/id_ed25519.pub)"`); outbound ssh to the box's high ports (some networks block them).

```bash
server/vast_run.sh up        # rents and starts the server; waits until /health says ready (~5-20 min)
server/vast_run.sh run       # the film on the box; open http://127.0.0.1:8765/ (Ctrl+C stops it)
server/vast_run.sh pull      # fetch the run's logs and clips into logs/live-box/
server/vast_run.sh down      # destroy the instance: always, or the meter keeps running
server/vast_run.sh status    # what is rented, and the server's /health
```

Renting bills by the hour from `up` until `down`. `VAST_QUERY` and `VAST_IMAGE` pick other offers and images (see the script's header). `run` copies only the client's keys (`OPENAI_*`, `LANGFUSE_*`, `LTX2_API_KEY`) into a `.env` on the box; `HF_TOKEN` reaches the server's environment. Both live on a machine someone else operates: see [Exposing it](#exposing-it-beyond-your-computer).

### B. Your own GPU machine, or another provider

Any Linux machine with a suitable GPU: your own, or an instance from another cloud. On that machine:

```bash
git clone https://github.com/artificial-realities/untitled-project.git
cd untitled-project
python3 -m venv .venv && source .venv/bin/activate
# torch 2.9.1 for CUDA 12.8 first (https://pytorch.org/get-started/previous-versions/), unless the image has it
pip install -r server/requirements.txt
cp .env.example .env                          # add HF_TOKEN (and OPENAI_API_KEY for the client)
set -a; . ./.env; set +a                      # the server reads only its environment
. server/server_defaults.sh && python server/ltx2_server.py    # serves on 127.0.0.1:8000
```

Then, in a second shell on the same machine, run the client (install `requirements.txt` into the same or another environment):

```bash
python main.py check --config configs/director.live.yaml
python main.py play --config configs/director.live.yaml --player web
```

and open the player through an ssh tunnel from your computer: `ssh -N -L 8765:localhost:8765 <user>@<gpu-host>`, then <http://127.0.0.1:8765/>.

To keep the client on your own computer instead (slower, see above), tunnel the server: `ssh -N -L 8000:localhost:8000 <user>@<gpu-host>`, and the default `ltxv_server_url` (`http://localhost:8000`) reaches it.

### C. Docker on a GPU machine

> Not yet run by the maintainers. It uses the same base image as route A.

Needs Docker with the NVIDIA Container Toolkit. With `HF_TOKEN` (and optionally `LTX2_API_KEY`) in `.env`:

```bash
docker compose --profile gpu up -d --build ltx-server    # first start: weights + warm-up
docker compose ps                                        # wait until ltx-server is (healthy)
docker compose logs -f ltx-server                        # or follow its log
```

The weights go to the `hf-cache` volume and survive restarts. The server is published on `127.0.0.1:8000`, so a client on the same machine (pip or Docker) reaches it. To run the film in the app container next to it, add `PLAY_CONFIG=configs/director.live.yaml` and `LTXV_SERVER_URL=http://ltx-server:8000` to `.env`, then `docker compose up app`. Without Compose: the build and run commands are at the top of [`server/Dockerfile`](../server/Dockerfile).

### Checking a server

`python main.py check --config configs/director.live.yaml` probes `/health` (free): reachable, ready, the right clip shape warmed, the key accepted, and the reference image fitting the render size. `python scripts/latency_probe.py --label <name>` renders a dozen clips and says whether a setting renders in real time; that costs GPU time.

## What is stored on disk

Everything the client writes goes under `logs/` (ignored by git; `--log-dir` moves it):

- `logs/<session>.md`: the transcript, including every line typed into the player.
- `logs/sessions/<session>/`: the world model (`world.json`, `events.jsonl`) and the timing log (`timing.jsonl`).
- `logs/video/`: in video mode, every rendered clip and the whole film concatenated at the end (`full_session.mp4`).

Nothing is deleted automatically. The server keeps the last clip's frames in memory only; the weights live in the Hugging Face cache (`~/.cache/huggingface`, or the `hf-cache` volume).

The client talks to OpenAI, to your LTX-2.5 server, and to Langfuse only if you set its keys. The player page loads nothing from other sites.

## Exposing it beyond your computer

Untitled Project is built for **trusted use**: you, on your own machines, with your own keys.

- **The web player has no accounts and no login.** Anyone who can reach it can watch, steer the film, and spend your OpenAI credit (each line is a Director call). It listens on `127.0.0.1` by default. `--host 0.0.0.0` makes it reachable from other devices: only do that on a network you trust. `/input` takes only JSON and at most 400 characters, so other web pages open in your browser can't post into it.
- **The LTX-2.5 server** listens on `127.0.0.1` by default. Reach it through an ssh tunnel or from the same machine. If it must listen on a network (`LTX2_HOST=0.0.0.0`), set `LTX2_API_KEY` on both sides and firewall the port. The key travels in a plain HTTP header, so on an untrusted network keep the connection inside ssh or HTTPS.
- **On the public internet,** put the player behind real authentication (for example a reverse proxy with login) and HTTPS, and set a hard budget limit in your OpenAI account. This setup is not provided or supported here.
- **Rented GPU boxes are someone else's machines.** The keys you send there (the client's `.env` written by `vast_run.sh run`, `HF_TOKEN` in the server's environment) are readable by whoever operates the host. Use an OpenAI project key with a budget limit, a read-only Hugging Face token, and `vast_run.sh down` when you're done. `vast_run.sh` connects with `StrictHostKeyChecking=no`, because every rented box has a new host key.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `OPENAI_API_KEY is not set` | Copy `.env.example` to `.env`, set the key, rerun. An empty `OPENAI_API_KEY` exported in your shell overrides `.env`. |
| `Missing Python package ...` | Activate the virtual environment and `pip install -r requirements.txt`. |
| The film shows nothing new, and the log says `OpenAI structured call failed` | Read the error after it: `401` is a wrong key, `429` / `insufficient_quota` means no credit or a rate limit, `model_not_found` means the model isn't available to your account (switch `director_model` / `showrunner_model` in a copy of the config). |
| `the LTX-2.5 server at ... is not reachable` | The server isn't running, the ssh tunnel is closed (`server/vast_run.sh status`), or `LTXV_SERVER_URL` / `ltxv_server_url` points elsewhere. From a container, the host is `host.docker.internal`. |
| `... is still loading` / `still warming` | Wait. The first start downloads the weights and compiles; the server's log (`/root/server.log` on a Vast box, `docker compose logs ltx-server`) shows progress. |
| `the server has not warmed [N] frames` | The server was started for another clip shape. Restart it with the `LTX2_WARMUP_FRAMES=...` the message gives (`vast_run.sh` passes it from `AR_CONFIG`, default the live config). |
| `requires a key: set LTX2_API_KEY` / `401` from the server | Set `LTX2_API_KEY` on the client to the server's value. |
| `reference image refused` | The live config takes only a reference at the render size's exact aspect (`i2v_reference_fit: strict`). Resize the image to 16:9, or use `i2v_reference_fit: pad` in your config. |
| The picture holds on a frame between clips | A clip wasn't ready in time: usually a slow Director call, or the client far from the GPU. Run the client on the GPU box; `logs/sessions/<id>/timing.jsonl` records each stall. |
| The server runs out of GPU memory | It needs about 72 GB resident. Try `LTX2_VAE_TILING=1`; offloading (`LTX2_OFFLOAD=text`) fits less memory but is not real time. |
| `Address already in use` (port 8765) | Another program uses the port: `--port 8766`. |
| `ffplay` not found | Use `--player web`, or install ffmpeg. |
| `vast_run.sh up`: `no offers match` | No H100 is available with the default query; set `VAST_QUERY` (see the script's header). |
| Opened the player from another device and nothing loads | By default it only listens on `127.0.0.1`. See [above](#exposing-it-beyond-your-computer). |
