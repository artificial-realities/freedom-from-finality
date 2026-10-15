# CLAUDE.md

Artificial Realities is an interactive generative movie platform — a continuously streaming AI-generated film whose story responds to viewer input while it plays. See [README.md](README.md) for the vision and the open problems, and [docs/decisions.md](docs/decisions.md) for why the design is what it is.

The current prototype reaches that through a deterministic engine that drives **two LLM roles on two clocks** (a Director that writes one beat of a few clips per call and a slow-clock Showrunner) over a **typed world model**, and renders each clip with LTX-2.5 just in time. **The orchestration is one mechanism under exploration, not the definition of the project** — treat it as replaceable.

## Layout

```
main.py                  # play command; --config, --story, --player, --segments, --log-dir
src/
  runner.py                  # run_live (the film: just-in-time render, viewer interrupts) · run_play (terminal, turn by turn)
  graph/director_engine.py   # DirectorEngine (graph: "director")
  agents/director.py         # one structured call per beat (BeatPlan: one action or state as 1-4 clips)
  agents/showrunner.py       # slow-clock compaction + planning (ShowrunnerUpdate)
  prompts/                   # director.*.md, showrunner.*.md
  story/                     # typed world model (world, with each entity's state), schemas, validator (per clip), context, compiler
  state/story_state.py       # StoryState — the runner-facing surface
  models/                    # config, story (blueprint), responses (the views the runner and player read)
  i2v/                       # image-to-video backends (base, ltxv, replay) and the clip shape
  llm/                       # LLM backends (openai, mock for offline tests)
  playback/player.py         # Playable + Player boundary of the live loop; TerminalPlayer (ffplay + stdin)
  web/                       # WebPlayer (aiohttp: SSE stream, /input, clips, /beacon) + player.html
  ui/terminal.py             # free-text input between clips (run_play)
  util/                      # json sanitizer, langfuse, media, prompt loader, story log, timing log
tests/                       # pytest: test_story, test_engine, test_render, test_web (mock LLM, no network, seconds)
scripts/latency_probe.py     # does an LTX-2.5 server setting render in real time
configs/                     # director.yaml (headless) · director.live.yaml (LTX-2.5) · director.replay.yaml (replayed clips)
stories/                     # per-story folders (story.json blueprint, reference image, assets); README.md: the field guide
docs/                        # ARCHITECTURE.md (how it works) · decisions.md (why, with the evidence)
server/                      # LTX-2.5 inference server (runs on a GPU box; see server/README.md)
```

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts. The world model (`src/story/world.py`) is the system of record; prose is never the source of truth.
- **Agents**: async functions, not classes. They read prompt sections from `src/story/context.py` and return schema instances; they never mutate the world. Only `src/story/validator.py` mutates the world.
- **Workflow, not agent system**: no tool use, no LLM-directed control flow. The engine decides order and triggers; the LLM proposes, code validates and applies.
- **Rendering is opt-out, content is not.** The Director writes the same beats, lines and world changes whether or not anything renders (`video_enabled`). Never gate content generation on rendering flags.
- **The video model's dialect lives in one place**: `src/story/compiler.py`. Schemas do not change when the model does.
- **Structured outputs are strict**: keep `BeatPlan` and `ShowrunnerUpdate` inside OpenAI's strict subset (no free-form dicts). Enforce structure in the validator, not the schema.
- **Prompts, schemas, compiler and engine are story-agnostic.** The Lego story is one blueprint; anything story-specific (looks, anchors, truths) lives in its story folder under `stories/`. Each story is a directory with `story.json`, a reference image, and any other assets. `--story stories/lego-void` (the default).
- **Facts, not thresholds, for story quality.** No code judges pacing, loops or whether the film is moving; give the roles what they need to judge it.
- **A product, not a research harness.** Experiments, benchmarks and evaluation tooling stay out of the repository; what one decides goes into `docs/decisions.md` in a few lines with its numbers. Tests cover contracts that break silently; four files do today. Comments say why in plain words, without tracker IDs, batch numbers or dates.

## Running

```bash
python main.py play --config configs/director.yaml                                        # headless dev — no video or audio
python main.py play --config configs/director.yaml --player web                           # the film as text in a browser; type to steer
python main.py play --config configs/director.live.yaml --player web                      # live video in the browser (needs the LTX-2.5 server)
python main.py play --config configs/director.replay.yaml --player web                    # the player with replayed clips from logs/video, no GPU
pytest                                                                                    # offline test suite (mock LLM)
```

Default to the headless config. Live runs need an LTX-2.5 server reachable at `ltxv_server_url` on a GPU box, so they are not available by default in this environment. The live film runs **on the GPU box**: `server/vast_run.sh up` rents it and starts the server, `server/vast_run.sh run` ships the working tree there, runs `main.py play --player web` next to the server and tunnels the player to `http://127.0.0.1:8765/`, `pull` fetches the logs and clips, `down` destroys the box. `--player web` serves the player at `http://127.0.0.1:8765/` (`--port`, `--host`); `--segments N` bounds a live run. Runs write a markdown transcript to `logs/` and the world model and a timing log (`timing.jsonl`: fires, renders, queue margins, stalls, viewer lines, Director calls, browser beacons) to `logs/sessions/<session>/`.

## How it behaves

- **The renderer.** LTX-2.5 distilled on its own recipe (`LTX2_VARIANT=distilled`: 8 sigmas, unguided), everything resident on an 80 GB card, fp8 matmuls and regional compile, 512×288. The full model's recipe on the distilled weights was what made figures drift within clips, and prompts never fixed it. The settings `vast_run.sh` starts with are in `server/server_defaults.sh`, every setting is in `server/ltx2_server.py`'s docstring, and `scripts/latency_probe.py` measures one. The client belongs on the GPU box (an MP4 through the ssh tunnel costs seconds).
- **One clip shape.** `clip_seconds: 6` becomes the run's one shape (`src/i2v/shape.py`): the Director is told its length, the engine records it, the render request carries its frames and the player holds its delivered frames; nothing else derives a length. Under fp8 each frame count compiles for minutes, so the server warms the client's shape at startup (`python -m src.i2v.shape <config>`, passed by `vast_run.sh`), reports it in `/health` `warm_frames`, and the client refuses to start without it.
- **One continuous picture.** Only a run's first clip is seeded from the story's reference image (the live config's `i2v_reference_fit: strict` takes only a reference at the render aspect to the pixel; the Lego story's is `legoman-16x9.png`), and the compiler opens that clip on what the reference `shows`. Every other clip is conditioned on the previous clip's last 9 frames. Re-seeding resets costume, props and position to the reference, so nothing re-seeds mid-film: after a server restart the client sends the last clip's tail (`ltxv_restart_recovery: "window"`) and falls back to the reference, telling the Director, only when the tail cannot be read. `ltxv_continuity_strength` trades continuity against steering. The identity anchor (`ltxv_anchor_strength`) is off: identity holds without it, an end anchor suppresses the closing action and a mid anchor inserts an idle beat.
- **Cuts are the story's choice.** A blueprint may allow cuts (`picture.perspective.cuts`, guidance in `cutting`); the Director then may set a clip's `cut_to`, compiled as "The view cuts to …" after the shot the picture is in, and the world tracks the current shot. Never on the film's first clip. The Lego story stays one continuous take: cuts land 2-4 s into the clip and repaint faces in close-ups.
- **Seams.** The live config's `seam: "overlap"` renders 153 frames (144 new + the 9-frame window) and holds 6.0 s; the page cuts where the next clip's repeated head begins and crossfades the sound over it, and the server keeps each clip under `audio_ceiling_lufs: -18` with its audio padded. `seam: "fade"` (145 frames, each file whole, a 250 ms crossfade) is the fallback.
- **Sound.** LTX-2.5 renders all of it, ambient and the characters' lines, from the compiled prompt. There is no narrator and no TTS: every spoken word is a character's `dialogue`, any character may speak, and a line is compiled as `Name says in a … voice, "line"` from the character's `voice`. Unverified on the GPU: two speakers in one clip, and a minifigure's printed face while it speaks.
- **Beats, rendered just in time.** A `BeatPlan` is one action or state as 1-4 clips (`max_clips_per_beat`), each with its own action, end state and entity ops, so the world advances clip by clip and a beat can be cut short after any clip. The engine emits clips with no LLM call; each render fires its estimate plus `buffer_seconds` (4.7 s) before its clip is due on air. The Director is called as soon as a beat's last clip is out (`plan_early`) or when the viewer speaks: the rest of the beat is dropped, a call in flight is cancelled and restarted with the line, and a line that arrives after the answer was written but before it played is joined to it and planned once. A line typed while clip N plays lands at N+2, or N+3 if it arrives after N+2's render fired. A beat that answers a line has at most `answer_max_clips` (2), and a deferred request is delivered by name (`BeatPlan.delivered_request`). `startup_clips` sets only the startup gate and the queue size. The Director's call is on the per-clip critical path, and its latency tail is the main stall risk.
- **Fail-soft rendering.** The previous render is known before the next clip is emitted. A failed render is retried once if it can still land in time; otherwise the world and the beat roll back to before the clip and the segment is replanned from the clip before (an emit exception too). Three failures in a row stop the film.
- **The Showrunner never blocks a segment.** Its update lands at the first plan that finds the task finished; runs are at least `showrunner_min_gap` segments apart and one at a time; held triggers are noted in the transcript, and a held `scene_done`/`needs_replan` is remembered until a replan run takes it. A run is triggered at the plan and spawned after the beat's first clip is applied, so its snapshot includes that clip. Its notes live in the user prompt so the system prompt stays cached.
- **The viewer outranks the scene card.** While a viewer's direction is active (for `viewer_momentum_seconds`, 90 s of film, or until the next line) the Director's copy of the scene card is paused, and only a card whose Showrunner trigger arose after the line clears the direction. The card is the menu for the quiet stretches. Both system prompts state the rule.
- **Pacing is the roles' judgment.** No code measures stagnation. The plan shows both roles how long the scene has run against `scene_seconds`; the Director ends a bit that keeps coming round, and the Showrunner judges the scene on every compaction pass.
- **The blueprint is the essential story, not a script.** `story.json` holds what the film is (`premise`, `question`, `engine`, `opening`, `truths`: what holds whatever the viewer does), who tells it (`storyteller`: `theme`, `tone`, `temperament`, `never`), who is in it (`locations`, `cast`) and how it looks (`picture`: `style`, `anchor`, `reference` with the image and what it `shows`, `perspective`). Empty fields add no prompt text. There is no time axis beyond the opening: the Showrunner writes the arc. Both roles read the film block and the truths; only the Showrunner reads `opening`.
- **The cast is the story's policy.** A blueprint names its protagonist (kept single and in the story by the validator), characters with `who`, `wants`, `nature`, `voice`, `look`, `look_short` and a starting state, supporting characters with entrance conditions (the Showrunner decides when, the Director stages), what kind of characters the film may introduce (`cast.newcomers`, empty for none; a newcomer is registered once with its look, who it is, voice and state) and its `perspective` (default and permitted switches, declared per beat in `BeatPlan.perspective`). The Lego story exercises all of it: Lego Man, the Spaceman (must appear, when a bit first fails badly), newcomers from any LEGO theme, a first-person switch through binoculars; one location.
- **One prose state per entity.** A character's pose, place and doing, or a prop's place, condition and who has it, is one line, replaced whole by the op that changes it. Fields stay structured only where code reads them (kind, present, gone, location).
- **The compiler** sends the full style and looks only on the opening clip and `picture.anchor` and `look_short` afterwards; an entity that enters the picture (introduced, or back after an absence) is described on that clip before the action and never listed as already on screen; the camera is a clause ("the camera remains static", "the camera makes a slow push-in …").
- **Models.** Both roles are gpt-5.6-luna at no reasoning (`director_model`, `showrunner_model`, `*_reasoning_effort: "none"`), about $0.50 per film-hour. It has run headless; the live performance numbers were measured with gpt-5.4-mini as Director.
- **Performance numbers** live in the README's performance table; do not restate them elsewhere.
