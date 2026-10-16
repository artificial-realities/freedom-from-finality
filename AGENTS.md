# AGENTS.md: working on Untitled Project

Project facts and rules for coding agents (and humans). Read this before changing anything. Setup details live in [docs/self-hosting.md](docs/self-hosting.md), the design in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), the reasons for it in [docs/decisions.md](docs/decisions.md), and the goal in [docs/vision.md](docs/vision.md).

## What this is

Untitled Project (a working title) is an experimental interactive video generation system published by Artificial Realities. It plays a continuously generated film while a viewer types suggestions into it: two LLM roles on two clocks write the film over a **typed world model** (a **Director** that writes one beat of a few clips per call, and a slow-clock **Showrunner** that compacts memory and plans the next scene), a deterministic engine validates and applies what they write, and LTX-2.5 renders each 6-second clip, with its sound, just in time on a GPU server. A browser page streams the clips and takes the viewer's lines.

**The orchestration is one mechanism under exploration, not the definition of the project**: treat it as replaceable.

It runs in two modes:

- **Text mode** (`configs/director.yaml`): no GPU and no video; the same film plays as text at story pace, in the terminal or the browser. Needs only an OpenAI API key.
- **Video mode** (`configs/director.live.yaml`): needs an LTX-2.5 server on an 80 GB NVIDIA GPU, reachable from the client (best: the client runs on the GPU box).

## Repository map

```
main.py                  # check | play; --config, --story, --player, --segments, --log-dir, --host, --port
src/
  preflight.py               # setup checks (`main.py check`; `play` runs them without the server probe)
  runner.py                  # run_live (the film: just-in-time render, viewer interrupts) · run_play (terminal, turn by turn)
  graph/director_engine.py   # DirectorEngine (graph: "director")
  agents/director.py         # one structured call per beat (BeatPlan: one action or state as 1-4 clips)
  agents/showrunner.py       # slow-clock compaction + planning (ShowrunnerUpdate)
  prompts/                   # director.*.md, showrunner.*.md
  story/                     # typed world model (world, with each entity's state), schemas, validator (per clip), context, compiler
  state/story_state.py       # StoryState — the runner-facing surface
  models/                    # config (Config; LTXV_SERVER_URL override), story (blueprint), responses (the views the runner and player read)
  i2v/                       # image-to-video backends (base, ltxv: the LTX-2.5 HTTP client) and the clip shape
  llm/                       # LLM backends (openai, mock for offline tests)
  playback/player.py         # Playable + Player boundary of the live loop; TerminalPlayer (ffplay + stdin)
  web/                       # WebPlayer (aiohttp: SSE stream, /input, clips, /beacon) + player.html
  ui/terminal.py             # free-text input between clips (run_play)
  util/                      # json sanitizer, langfuse, media, prompt loader, story log, timing log
tests/                       # pytest: test_story, test_engine, test_render, test_web, test_setup (offline, seconds)
scripts/latency_probe.py     # does an LTX-2.5 server setting render in real time (renders on the GPU)
configs/                     # director.yaml (text mode) · director.live.yaml (LTX-2.5)
stories/                     # per-story folders (story.json blueprint, reference image, assets); README.md: the field guide
docs/                        # self-hosting.md · ARCHITECTURE.md (how it works) · decisions.md (why) · vision.md (the goal)
server/                      # LTX-2.5 inference server for the GPU box: ltx2_server.py, requirements.txt, Dockerfile,
                             #   server_defaults.sh, vast_run.sh (rent on Vast.ai), remote_setup.sh; see server/README.md
Dockerfile, compose.yaml     # the client image; the `gpu` profile adds the server image (server/Dockerfile)
.github/                     # CI workflow, CONTRIBUTING.md, SECURITY.md
logs/                        # run output (git-ignored): transcripts, sessions/<id>/ (world model, timing.jsonl), clips
```

## Commands

Run everything from the repository root inside a virtual environment.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt                    # runtime + pytest + ruff
python -m pytest                                       # offline suite (mock LLM, no network beyond loopback, no key)
ruff check .                                           # lint (rules in pyproject.toml)
python main.py check --config configs/director.yaml    # setup check, no paid calls; exit 1 names what is missing
python main.py check --config configs/director.live.yaml   # also probes the LTX-2.5 server's /health (free)

# Paid: these call OpenAI (see the rules below)
python main.py play --config configs/director.yaml                       # text, turn by turn in the terminal
python main.py play --config configs/director.yaml --player web          # text in the browser at http://127.0.0.1:8765/
python main.py play --config configs/director.live.yaml --player web     # live video (needs the LTX-2.5 server)
docker compose up --build                                                # the text film in a container (needs .env)
```

`--segments N` bounds a run; Ctrl+C stops it. Supported Python: 3.11-3.14 (CI runs 3.11 and 3.14; `main.py` refuses below 3.10). There is no formatter or type checker configured; don't reformat files you aren't changing.

## Setting the project up for someone

When asked to "set this up so I can run it", work through these steps and stop where the human has to act.

1. **Ask which mode** they want: text mode (any computer, an OpenAI key) or video mode (a GPU). Don't assume they have, or want to rent, a GPU.
2. **Prerequisites:** `python3 --version` (3.11+), and for video mode `ffmpeg -version` (ffmpeg and ffprobe on the client; on the GPU box too). Create `.venv` and `pip install -r requirements.txt` (or `requirements-dev.txt` to run the tests).
3. **Keys:** `cp .env.example .env` if it doesn't exist. The human puts their keys into `.env` themselves (where to get each: [docs/self-hosting.md](docs/self-hosting.md#configuration)). Never ask for a key in chat and never open `.env` to check it.
4. **Check:** `python main.py check --config <config>` and fix what it reports; `python -m pytest` confirms the install. Both are free.
5. **First run:** `play` costs money (OpenAI calls). Ask before starting it, or give the human the command. Suggest `--segments 10` for a first run.
6. **Video mode:** pick a GPU route with the human ([docs/self-hosting.md](docs/self-hosting.md#gpu-server-video-mode)): renting on Vast.ai with `server/vast_run.sh` (billed by the hour, needs their `VAST_API_KEY`, the `vastai` CLI and an ssh key registered on Vast), or their own GPU machine (pip or `server/Dockerfile`). The weights are gated: the human accepts the LTX-2.5 licence on Hugging Face and creates `HF_TOKEN`. `python main.py check --config configs/director.live.yaml` says "Ready to play" when the client can use the server.

## Secrets and configuration

- Secrets are environment variables, read from the shell or from `.env` in the repo root (`.env.example` is the template, and documents each one). `.env` is git-ignored and excluded from Docker builds.
  - `OPENAI_API_KEY`: required for every config.
  - `HF_TOKEN`: on the GPU machine only, to download the gated weights.
  - `VAST_API_KEY`: only for `server/vast_run.sh`.
  - `LTX2_API_KEY`: optional shared key between the client and the LTX-2.5 server (the server then refuses `/generate` and `/reset` without it).
  - `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`: optional tracing; when set, every prompt and output is sent to Langfuse.
- **Never print, log, commit, echo or paste a key or the contents of `.env`.** Use `python main.py check`, which reports only whether a key is set. If one is missing, tell the human where to create it and to put it in `.env` themselves.
- Everything else is in the config YAML (`Config` in `src/models/config.py`, every field commented) and the story's `story.json`. `LTXV_SERVER_URL` overrides the config's `ltxv_server_url`. `vast_run.sh` and the server read `LTX2_*` settings (listed in `server/ltx2_server.py`'s docstring). If you add a variable, update `.env.example`, the table in [docs/self-hosting.md](docs/self-hosting.md#configuration) and this file.
- Models: both roles are `gpt-5.6-luna` at no reasoning (`director_model`, `showrunner_model`, `*_reasoning_effort: "none"`). `model` is only the fallback for a role without its own. Don't switch models without the owner's agreement and a real test.

## Paid calls and rented GPUs: rules

- **Never run `python main.py play` with an OpenAI config, or start a film in the browser, without the human's explicit permission for that run.** Each beat is a Director call and each viewer line another; the two roles cost about $0.50 per hour of film with `gpt-5.6-luna`.
- **Never run `server/vast_run.sh up` (or anything that rents, starts or restarts a GPU) without explicit approval.** A rented GPU bills by the hour until `server/vast_run.sh down` destroys it; after any GPU work, confirm with `server/vast_run.sh status` that nothing is left running. `scripts/latency_probe.py` renders on the server (GPU time).
- Tests, CI and `check` stay offline and free. The suite's `offline` fixture (`tests/conftest.py`) fails any connection beyond loopback and clears keys from the environment. Fake the models the way the tests do: the mock LLM (`llm_backend: "mock"`), `Clips` in `tests/test_engine.py` for rendering, `_Health` in `tests/test_render.py` for the server, `_serve` in `tests/test_setup.py` for a local HTTP stand-in.
- A real check of the model's behaviour (a headless film, a live film) needs the human's key, go-ahead and attention. Report it as untested otherwise.

## Verifying changes without paid calls

1. `ruff check .` and `python -m pytest` pass.
2. `python main.py check --config configs/director.yaml` with no key exits 1 and names `OPENAI_API_KEY`.
3. Engine, prompt, schema or compiler changes: the mock backend runs the whole loop offline (`tests/test_engine.py`, `tests/test_web.py`). Whether the real models write a better film can only be judged in a paid run: say so.
4. Server changes: `python -m py_compile server/*.py` and `bash -n server/*.sh`. Anything beyond that needs a GPU: report it as unverified.

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts. The world model (`src/story/world.py`) is the system of record; prose is never the source of truth.
- **Agents**: async functions, not classes. They read prompt sections from `src/story/context.py` and return schema instances; they never mutate the world. Only `src/story/validator.py` mutates the world.
- **Workflow, not agent system**: no tool use, no LLM-directed control flow. The engine decides order and triggers; the LLM proposes, code validates and applies.
- **Rendering is opt-out, content is not.** The Director writes the same beats, lines and world changes whether or not anything renders (`video_enabled`). Never gate content generation on rendering flags.
- **The video model's dialect lives in one place**: `src/story/compiler.py`. Schemas do not change when the model does.
- **Structured outputs are strict**: keep `BeatPlan` and `ShowrunnerUpdate` inside OpenAI's strict subset (no free-form dicts). Enforce structure in the validator, not the schema.
- **Prompts, schemas, compiler and engine are story-agnostic.** The Lego story is one blueprint; anything story-specific (looks, anchors, truths) lives in its story folder under `stories/`. Each story is a directory with `story.json`, a reference image, and any other assets. `--story stories/lego-void` (the default).
- **Facts, not thresholds, for story quality.** No code judges pacing, loops or whether the film is moving; give the roles what they need to judge it.
- **A product, not a research harness.** Experiments, benchmarks and evaluation tooling stay out of the repository; what one decides goes into `docs/decisions.md` in a few lines with its numbers. Tests cover contracts that break silently; five files do today. Comments say why in plain words, without tracker IDs, batch numbers or dates.
- **Safe by default.** The web player and the LTX-2.5 server listen on `127.0.0.1`; never change a default to listen publicly or document public hosting as safe. Neither has accounts: the player has no login at all, and the server only the optional shared key. The app writes only under `--log-dir` (default `logs/`); a value from a request never becomes a path. Don't add telemetry or third-party calls.

## How it behaves

- **The renderer.** LTX-2.5 distilled on its own recipe (`LTX2_VARIANT=distilled`: 8 sigmas, unguided), everything resident on an 80 GB card, fp8 matmuls and regional compile, 512×288. The full model's recipe on the distilled weights was what made figures drift within clips, and prompts never fixed it. The settings the server starts with are in `server/server_defaults.sh`, every setting is in `server/ltx2_server.py`'s docstring, and `scripts/latency_probe.py` measures one. The client belongs on the GPU box (an MP4 through the ssh tunnel costs seconds).
- **One clip shape.** `clip_seconds: 6` becomes the run's one shape (`src/i2v/shape.py`): the Director is told its length, the engine records it, the render request carries its frames and the player holds its delivered frames; nothing else derives a length. Under fp8 each frame count compiles for minutes, so the server warms the client's shape at startup (`python -m src.i2v.shape <config>`, passed by `vast_run.sh`; `server_defaults.sh` defaults to the live config's), reports it in `/health` `warm_frames`, and the client refuses to start without it.
- **One continuous picture.** Only a run's first clip is seeded from the story's reference image (the live config's `i2v_reference_fit: strict` takes only a reference at the render aspect to the pixel; the Lego story's is `legoman-16x9.png`), and the compiler opens that clip on what the reference `shows`. Every other clip is conditioned on the previous clip's last 9 frames. Re-seeding resets costume, props and position to the reference, so nothing re-seeds mid-film: after a server restart the client sends the last clip's tail (`ltxv_restart_recovery: "window"`) and falls back to the reference, telling the Director, only when the tail cannot be read. `ltxv_continuity_strength` trades continuity against steering. The identity anchor (`ltxv_anchor_strength`) is off: identity holds without it, an end anchor suppresses the closing action and a mid anchor inserts an idle beat.
- **Cuts are the story's choice.** A blueprint may allow cuts (`picture.perspective.cuts`, guidance in `cutting`); the Director then may set a clip's `cut_to`, compiled as "The view cuts to …" after the shot the picture is in, and the world tracks the current shot. Never on the film's first clip. The Lego story stays one continuous take: cuts land 2-4 s into the clip and repaint faces in close-ups.
- **Seams.** The live config's `seam: "overlap"` renders 153 frames (144 new + the 9-frame window) and holds 6.0 s; the page cuts where the next clip's repeated head begins and crossfades the sound over it, and the server keeps each clip under `audio_ceiling_lufs: -18` with its audio padded. `seam: "fade"` (145 frames, each file whole, a 250 ms crossfade) is the fallback.
- **Sound.** LTX-2.5 renders all of it, ambient and the characters' lines, from the compiled prompt. There is no narrator and no TTS: every spoken word is a character's `dialogue`, any character may speak, and a line is compiled as `Name says in a … voice, "line"` from the character's `voice`. Unverified on the GPU: two speakers in one clip, and a minifigure's printed face while it speaks.
- **Beats, rendered just in time.** A `BeatPlan` is one action or state as 1-4 clips (`max_clips_per_beat`), each with its own action, end state and entity ops, so the world advances clip by clip and a beat can be cut short after any clip. The engine emits clips with no LLM call; each render fires its estimate plus `buffer_seconds` (4.7 s) before its clip is due on air. The Director is called as soon as a beat's last clip is out (`plan_early`) or when the viewer speaks: the rest of the beat is dropped, a call in flight is cancelled and restarted with the line, and a line that arrives after the answer was written but before it played is joined to it and planned once. A line typed while clip N plays lands at N+2, or N+3 if it arrives after N+2's render fired. A beat that answers a line has at most `answer_max_clips` (2), and a deferred request is delivered by name (`BeatPlan.delivered_request`). `startup_clips` sets only the startup gate and the queue size. The Director's call is on the per-clip critical path, and its latency tail is the main stall risk.
- **Fail-soft rendering.** The previous render is known before the next clip is emitted. A failed render is retried once if it can still land in time; otherwise the world and the beat roll back to before the clip and the segment is replanned from the clip before (an emit exception too). Three failures in a row stop the film.
- **The Showrunner never blocks a segment.** Its update lands at the first plan that finds the task finished; runs are at least `showrunner_min_gap` segments apart and one at a time; held triggers are noted in the transcript, and a held `scene_done`/`needs_replan` is remembered until a replan run takes it. A run is triggered at the plan and spawned after the beat's first clip is applied, so its snapshot includes that clip. Its notes live in the user prompt so the system prompt stays cached.
- **The viewer outranks the scene card.** While a viewer's direction is active (for `viewer_momentum_seconds`, 90 s of film, or until the next line) the Director's copy of the scene card is paused, and only a card whose Showrunner trigger arose after the line clears the direction. The card is the menu for the quiet stretches. Both system prompts state the rule. A line is a suggestion to the storyteller, not a command: it changes what happens from the beat it lands in, in the story's and the characters' own way (a twist, a character who resists, a consequence later).
- **Characters want things; the story progresses.** Each character acts in its own nature, coloured by its `wants` (a newcomer's `who` says what it wants), and both roles are asked to keep the story moving without a rule for what each scene must bring.
- **Pacing is the roles' judgment.** No code measures stagnation. The plan shows both roles how long the scene has run against `scene_seconds`; the Director ends a bit that keeps coming round, and the Showrunner judges the scene on every compaction pass.
- **The blueprint is the essential story, not a script.** `story.json` holds what the film is (`premise`, `question`, `engine`, `opening`, `truths`: what holds whatever the viewer does), who tells it (`storyteller`: `theme`, `tone`, `temperament`, `never`), who is in it (`locations`, `cast`) and how it looks (`picture`: `style`, `anchor`, `reference` with the image and what it `shows`, `perspective`). Empty fields add no prompt text. There is no time axis beyond the opening: the Showrunner writes the arc. Both roles read the film block and the truths; only the Showrunner reads `opening`.
- **The cast is the story's policy.** A blueprint names its protagonist (kept single and in the story by the validator), characters with `who`, `wants`, `nature`, `voice`, `look`, `look_short` and a starting state, supporting characters with entrance conditions (the Showrunner decides when, the Director stages), what kind of characters the film may introduce (`cast.newcomers`, empty for none; a newcomer is registered once with its look, who it is and what it wants, voice and state) and its `perspective` (default and permitted switches, declared per beat in `BeatPlan.perspective`). The Lego story exercises all of it: Lego Man, the Spaceman (must appear, when a bit first fails badly), newcomers from any LEGO theme, a first-person switch through binoculars; one location.
- **One prose state per entity.** A character's pose, place and doing, or a prop's place, condition and who has it, is one line, replaced whole by the op that changes it. Fields stay structured only where code reads them (kind, present, gone, location).
- **The compiler** sends the full style and looks only on the opening clip and `picture.anchor` and `look_short` afterwards; an entity that enters the picture (introduced, or back after an absence) is described on that clip before the action and never listed as already on screen; the camera is a clause ("the camera remains static", "the camera makes a slow push-in …").
- **Models.** Both roles are gpt-5.6-luna at no reasoning, about $0.50 per film-hour. It has run headless; the live performance numbers were measured with gpt-5.4-mini as Director.
- **Performance numbers** live in the README's performance table; do not restate them elsewhere.

## Troubleshooting

See the table in [docs/self-hosting.md](docs/self-hosting.md#troubleshooting). Start with `python main.py check --config <config>`; then the run's transcript (`logs/*.md`) and timing log (`logs/sessions/<session>/timing.jsonl`), and on the GPU box the server's log. The usual suspects are a missing key, the LTX-2.5 server still loading or unreachable (tunnel down, wrong `LTXV_SERVER_URL`), a shape the server did not warm, and the client running far from the GPU (stalls).

## Definition of done

- A focused diff that does one thing, with no unrelated reformatting.
- `ruff check .` and `python -m pytest` pass, with tests added for new behaviour that could break silently.
- No secrets in code, logs, test fixtures or commit messages, and no paid API calls or GPU rentals unless the human authorised them.
- Docs updated when behaviour, configuration or setup changes: README.md, docs/, `.env.example`, `server/README.md` and this file.
- State plainly what was verified and what wasn't (for example "suite and check pass; not run on a GPU; no paid film").
