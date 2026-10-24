# AGENTS.md: working on Freedom from Finality

Project facts and rules for coding agents (and humans). How the story works is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and why it is built that way in [docs/decisions.md](docs/decisions.md).

## What this is

An infinite, continuously generated story that a viewer steers by typing suggestions into a browser page. Two LLM roles — **Now** (the creative voice, one beat per call on the critical path) and **Then** (memory and intuition, off the critical path on a slow clock) — write the story over a typed world model; a deterministic engine validates and applies what they write; LTX-2.5 renders each 6-second clip, with its sound, just in time on a GPU server. The orchestration is one mechanism under exploration, not the definition of the project: treat it as replaceable.

Each viewer has one adventure, which never ends: it begins once from the frame zero and resumes from the last clip they saw, in the `adventure/` folder on their own computer (see "The adventure" in ARCHITECTURE.md). `configs/video.yaml` is how it is played (the default; it needs the LTX-2.5 server on an 80 GB NVIDIA GPU, best with the client on the same machine). `configs/text.yaml` is a testing tool, never for viewers: the story as text, no GPU, only an OpenAI key, played in its own test adventure (`logs/test-adventure`), never the viewer's.

## Repository map

```
main.py                    # check | play | adopt; --config, --adventure, --story, --segments, --log-dir, --host, --port
src/
  adventure.py             # the adventure on disk: begin, resume, commit as of each aired clip, checkpoints,
                           # branches, crash recovery, adopt
  runner.py                # run_live: the story loop (just-in-time render, viewer interrupts, commit on air)
  engine.py                # NowEngine: plan a beat, emit its clips, roll back, the Then clock
  agents/                  # now.py (BeatPlan per beat), then.py (ThenUpdate)
  prompts/                 # now.*.md, then.*.md, policy.md (the content policy, the same in every adventure)
  story/                   # typed world model, viewer's memory, schemas, validator (per clip), context assembler, prompt compiler
  state/story_state.py     # StoryState: the runner-facing surface
  models/                  # config (Config, every field commented), story (FrameZero), responses
  i2v/                     # the renderer boundary, the LTX-2.5 HTTP client, the clip shape
  llm/                     # the OpenAI backend
  playback/player.py       # Playable + Player: the loop's boundary to the page
  web/                     # WebPlayer (aiohttp: SSE, /input, clips) + player.html
  preflight.py             # setup checks (`main.py check`)
  util/                    # durable writes, media probe, prompt loader, transcript, timing log
configs/                   # text.yaml · video.yaml
frame_zero.json            # the first frame a new adventure begins from (read only then): scene, character, location, image
docs/                      # ARCHITECTURE.md (how) · decisions.md (why)
server/                    # the LTX-2.5 server for the GPU box, and vast_run.sh to rent one; see server/README.md
adventure/                 # the viewer's adventure (git-ignored, personal): see src/adventure.py
logs/                      # technical run output (git-ignored): transcripts, sessions/<id>/, test-adventure/
```

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt                    # runtime + ruff
ruff check .                                           # lint (rules in pyproject.toml)
python main.py check                                   # setup and adventure check, no paid calls; probes the
                                                       # LTX-2.5 server's /health (free); exit 1 names what is missing
python main.py check --config configs/text.yaml        # the same without a server (the test adventure)

# Paid: these call OpenAI (see the rules below)
python main.py play                                    # the viewer's adventure, as video at http://127.0.0.1:8765/
python main.py play --config configs/text.yaml         # testing: the story as text, in logs/test-adventure
```

`--segments N` bounds a run; Ctrl+C stops it. Python 3.11+. There is no formatter or type checker; don't reformat files you aren't changing.

## Secrets and configuration

- Secrets are environment variables, from the shell or from `.env` in the repo root (`.env.example` is the template). `.env` is git-ignored. `OPENAI_API_KEY` is required for every config; `HF_TOKEN` is for the GPU machine (the gated weights); `VAST_API_KEY` only for `server/vast_run.sh`; `LTXV_SERVER_URL` overrides the config's server address.
- **Never print, log, commit, echo or paste a key or the contents of `.env`.** `python main.py check` reports only whether a key is set. If one is missing, tell the human where to create it and to put it in `.env` themselves.
- Everything else is in the config YAML (`Config` in `src/models/config.py`; an unknown key is an error) and `frame_zero.json`. Both roles are `gpt-5.6-luna` at no reasoning; don't switch models without the owner's agreement and a real test.

## Paid calls and rented GPUs: rules

- **Never run `python main.py play` or start a film in the browser without the human's explicit permission for that run.** Each beat is a Now call and each viewer line another, about $0.50 per hour of film.
- **Never run `server/vast_run.sh up` (or anything that rents, starts or restarts a GPU) without explicit approval.** A rented GPU bills by the hour until `server/vast_run.sh down`; after any GPU work, confirm with `server/vast_run.sh status` that nothing is left running.
- CI and `check` stay offline and free.

## Verifying changes

1. `ruff check .` passes.
2. Engine, prompt, schema or compiler changes: nothing runs the loop offline, so they are seen working only in a paid run: say so. Never test against the viewer's `adventure/`; a test run uses its own `--adventure` folder.
3. Server changes: `python -m py_compile server/*.py` and `bash -n server/*.sh`; anything beyond that needs a GPU, so report it as unverified.

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`, not `TypedDict` or raw dicts. The world model (`src/story/world.py`) is the system of record for one timeline; the viewer's memory (`src/story/viewer.py`) is the person's, append-only and outside every timeline. Prose is never the source of truth.
- **Agents** are async functions, not classes. They read prompt sections from `src/story/context.py` and return schema instances; only `src/story/validator.py` mutates the world.
- **Workflow, not agent system**: no tool use, no LLM-directed control flow. The engine decides order and triggers; the LLM proposes, code validates and applies.
- **Rendering is opt-out, content is not.** Now writes the same beats, lines and world changes whether or not anything renders. Never gate content generation on `video_enabled`.
- **The video model's dialect lives in one place**: `src/story/compiler.py`. Schemas do not change when the model does.
- **Structured outputs are strict**: keep `BeatPlan` and `ThenUpdate` inside OpenAI's strict subset (no free-form dicts); enforce structure in the validator.
- **Prompts, schemas, compiler and engine are story-agnostic.** The opening scene lives in `frame_zero.json` and becomes ordinary world state; nothing in it is a standing rule. What holds in every adventure is the content policy (`src/prompts/policy.md`).
- **Facts, not thresholds, for story quality.** No code judges pacing, loops or whether the film is moving; give the roles what they need to judge it.
- **No test suite, by choice.** The project changes too fast for tests to pay their upkeep; `ruff check .` is the gate. Don't add tests or test-only code paths unless the owner asks.
- **A product, not a research harness.** Experiments, benchmarks and evaluation tooling stay out of the repository; what one decides goes into `docs/decisions.md` in a few lines with its numbers. Comments say why in plain words, without tracker IDs or dates.
- **Safe by default.** The player and the LTX-2.5 server listen on `127.0.0.1` and have no login; never change that default or document public hosting as safe. The app writes only under `--log-dir` and `--adventure`; a value from a request never becomes a path. No telemetry or third-party calls.
- **One clip shape, one continuous picture.** `clip_seconds` becomes the run's one shape (`src/i2v/shape.py`), which every consumer reads and the server must have warmed. Only an adventure's first clip is seeded from the reference image; a resume continues from the frames saved with the last clip; never re-seed mid-film (it resets costume, props and position).
- **The adventure belongs to the viewer and lasts.** It is saved as of the last clip they saw and holds the story, never the rendered video (clips are deleted once played; only the frames the next clip continues from are kept). Only `src/adventure.py` and the viewer's memory write to it, durably (`src/util/durable.py`); keep it portable (relative paths, JSON, JSON lines, the format number in `adventure.json`) and outside the world model what belongs to the person.

## Definition of done

- A focused diff, with no unrelated reformatting.
- `ruff check .` passes.
- No secrets in code, logs, test fixtures or commit messages, and no paid calls or GPU rentals unless the human authorised them.
- Docs updated when behaviour, configuration or setup changes: README.md, docs/, `.env.example`, `server/README.md` and this file.
- State plainly what was verified and what wasn't (for example "suite and check pass; not run on a GPU; no paid film").
