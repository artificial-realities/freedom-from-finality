# CLAUDE.md

Artificial Realities is an interactive generative movie platform — a continuously streaming AI-generated film whose story responds to viewer input while it plays. See [README.md](README.md) for the vision and the open problems.

The current prototype reaches that through a deterministic engine that drives **two LLM roles on two clocks** (a Director that writes one beat of a few clips per call and a slow-clock Showrunner) over a **typed world model**, and renders each clip with LTX-2.5 just in time. **The orchestration is one mechanism under exploration, not the definition of the project** — treat it as replaceable.

## Layout

```
main.py                  # play command; --config, --story, --player, --segments, --log-dir
src/
  graph/director_engine.py   # DirectorEngine — current design (graph: "director")
  agents/director.py         # one structured call per beat (BeatPlan: one action or state as 1-4 clips)
  agents/showrunner.py       # slow-clock compaction + planning (ShowrunnerUpdate)
  prompts/                   # director.*.md, showrunner.*.md
  story/                     # typed world model (world, with character state), schemas, validator (per clip), context, compiler, pacing (story seconds)
  state/story_state.py       # StoryState — runner-facing surface shared by both engines
  models/                    # config, story (blueprint), responses (legacy views)
  i2v/                       # image-to-video backends (base, ltxv, replay)
  llm/                       # LLM backends (openai, mock for offline tests)
  tts/elevenlabs.py          # opt-in voice-over TTS (narration: "tts")
  playback/mux.py            # ffmpeg concat + audio mux
  playback/player.py         # Playable + Player boundary of the live loop; TerminalPlayer (ffplay + stdin)
  web/                       # WebPlayer (aiohttp: SSE stream, /input, clips, /beacon) + player.html
  eval/runner.py             # run_live (the film: just-in-time render, viewer interrupts) · run_play (terminal, turn by turn)
  ui/terminal.py             # free-text input between clips (run_play)
  util/                      # json sanitizer, langfuse, media, prompt loader, story log, timing log
tests/                       # python -m tests.run (no pytest needed)
scripts/steer.py             # scripted viewer: posts lines to a running web player at given segments
scripts/latency_probe.py     # seconds per clip second for one server setting (the real-time work)
configs/                     # director.yaml (headless) · director.live.yaml (LTX-2.5) · director.replay.yaml (replayed clips)
stories/                     # per-story folders (story.json blueprint, reference image, assets)
data/                        # legacy path for the default story (still works; stories/ is preferred)
docs/                        # ARCHITECTURE.md · reviews/ · legacy/
server/                      # LTX-2.5 inference server (runs on a GPU box; see server/README.md)
```

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts. The world model (`src/story/world.py`) is the system of record; prose is never the source of truth.
- **Agents**: async functions, not classes. They read prompt sections from `src/story/context.py` and return schema instances; they never mutate the world. Only `src/story/validator.py` mutates the world.
- **Workflow, not agent system**: no tool use, no LLM-directed control flow. The engine decides order and triggers; the LLM proposes, code validates and applies.
- **Rendering is opt-out, content is not.** The Director produces the voice-over field whenever the gate is open; only its rendering depends on `narration` / `audio_enabled`. Never gate content generation on rendering flags.
- **The video model's dialect lives in one place**: `src/story/compiler.py`. Schemas do not change when the model does.
- **Structured outputs are strict**: keep `BeatPlan` and `ShowrunnerUpdate` inside OpenAI's strict subset (no free-form dicts). Enforce structure in the validator, not the schema.
- **Prompts, schemas, compiler and engine are story-agnostic.** The Lego story is one blueprint; anything story-specific (looks, anchors, rules) lives in its story folder under `stories/`. Each story is a directory with `story.json`, a reference image, and any other assets. `--story stories/lego-void` (the default).

## Running

```bash
python main.py play --config configs/director.yaml                                        # headless dev — no video or audio
python main.py play --config configs/director.yaml --player web                           # the film as text in a browser; type to steer
python main.py play --config configs/director.live.yaml --player web                      # live video in the browser (needs the LTX-2.5 server)
python main.py play --config configs/director.replay.yaml --player web                    # the player with replayed clips from logs/video, no GPU
python -m tests.run                                                                       # offline test suite (mock LLM)
```

Default to the headless config. Live runs need an LTX-2.5 server reachable at `ltxv_server_url` on a GPU box, so they are not available by default in this environment. The live film runs **on the GPU box**: `server/vast_run.sh up` rents it and starts the server, `server/vast_run.sh run` ships the working tree there, runs `main.py play --player web` next to the server and tunnels the player to `http://127.0.0.1:8765/`, `pull` fetches the logs and clips, `down` destroys the box. `--player web` serves the player at `http://127.0.0.1:8765/` (`--port`, `--host`); `--segments N` bounds a live run. Runs write a markdown transcript to `logs/` and the world model and a timing log (`timing.jsonl`: fires, renders, queue margins, stalls, viewer lines, Director calls, browser beacons) to `logs/sessions/<session>/`.

## Current constraints

- **Within-clip identity drift was the sampling recipe, not the prompts.** Every run before 2026-09-15 ran the distilled transformer with the full-model recipe (CFG 3, STG, 15 linear steps); on the correct distilled recipe (`LTX2_VARIANT=distilled`, 8 sigmas, unguided) the figure's torso, legs and ground hold through the action with no anchor, and the Director continues across segments instead of cutting every one (`docs/reviews/2026-09-15-substrate-sweep.md`). The Director wrote eyebrows, frowns and smirks and the model painted them; the blueprint now forbids facial expressions and the verification run shows plain faces. Real residual drift is a greying ground at the end of the busiest clips. Cuts were removed on 2026-09-17: the Director writes one continuous picture, and the renderer seeds only the first clip of a run (and the first clip after a server restart) from the letterboxed reference, which the compiler opens from `reference_pose`. A re-seed resets costume, props and position to the reference's state (the red cap vanished at a cut on 2026-09-15; a re-entry cut on 2026-09-17 put a second figure on screen for a moment), so nothing re-seeds mid-film. A viewer's line typed while clip N plays lands at N+2 with `lead_clips: 0`: the live loop starts the Director call for N+2 as late as the render of N+1 allows and reads the lines then (`run_live`, late start). The live config now buffers one clip (`lead_clips: 1`), which makes it about N+3, and fixes every clip at 6 s (`clip_seconds: 6`: the Director is told to plan for 6 s and the compiler returns 6 whatever the shots add up to), so the server compiles one shape and the pacing is constant.
- **`ltxv_continuity_strength`** trades continuity against steering for `continue` shots. At `1.0` new prompts barely land; lower values let steering through at the cost of visual continuity.
- **The server picks the weights and the recipe with `LTX2_VARIANT`** (`server/ltx2_server.py`): `distilled` (default) is the 8-sigma unguided recipe on `transformer/`; `full` loads `transformer_full/` at 30 steps with guidance and shifting, and on 2026-09-15 gave the same identity at 3.5× the time with an extra-character artefact, so it is not used. Placement is `LTX2_OFFLOAD`: `gpu` (default) keeps everything resident with the prompt enhancer dropped (70 GB on an H100 PCIe; 12–13 s per 5 s clip and 17–23 s per 6–8 s clip, of which the 8 transformer forwards are 11–19 s, plus 1.2–1.8 s to encode; a fresh box adds 10–20 s of CPU warm-up to its first three clips); `text` offloads only the Gemma text encoder (28 s); `cpu` offloads everything (57 s). The server logs a per-stage breakdown (`timings:`) each turn and encodes the MP4 through ffmpeg (diffusers' PyAV path took 17–24 s per clip). `server/vast_run.sh` rents and tears down a GPU on Vast.ai (`up`, `restart` to switch any `LTX2_*` setting without re-renting, `down`); it defaults to an H100 SXM or NVL, which are cheaper and faster than the PCIe. Speed flags on the server (`LTX2_ATTN`, `LTX2_QUANT`, `LTX2_COMPILE`, `LTX2_WIDTH/HEIGHT`, `LTX2_ENCODER`, warm-up at startup) and the plan behind them: `docs/reviews/2026-09-16-real-time-plan.md`; `scripts/latency_probe.py` measures a setting.
- **Real time was measured on 2026-09-16** (`docs/reviews/2026-09-16-real-time-plan.md`, `logs/probe/`): with `LTX2_QUANT=fp8 LTX2_COMPILE=1` an H100 NVL generates a 5 s clip at 640×352 in 3.5 s + 0.6 s encode (0.81× real time) and a B200 does 768×416 in 3.0 s + 0.8 s (0.76×); 960×544 stays above real time on both (1.19×). The forwards are compute-bound (compile 1.22×, fp8 1.27× on Hopper, tokens linear); the newer diffusers VAE decode needs 25–40 GB untiled and the server tiles it on demand; FlashAttention-3 refuses LTX-2's attention masks; NVENC is unavailable in Vast containers; under fp8 each clip shape compiles separately (90–170 s), so a live run wants one clip length; the MP4 through the ssh tunnel costs 1.5–4.6 s per clip, so the client belongs on the GPU box.
- **Identity anchor**: `ltxv_anchor_strength` conditions every clip on the reference image as a guiding keyframe at `ltxv_anchor_index` (latent frames, -1 = last), on top of the index-0 condition. Off: it is not needed for identity on the correct recipe, an end anchor suppresses the closing action and a mid anchor inserts an idle beat.
- **LTX-2.5 renders native ambient audio**, so clips already carry an audio track; with `narration: "tts"` the ElevenLabs line is mixed over it (ducked), not substituted.
- **The frame observer and the evaluation tooling were removed on 2026-09-16.** The observer (a vision check of every rendered clip that showed the Director a "Picture" section and forced a cut on drift) measured a drift the recipe fix had already removed, its forced cut never fired in any run, and its rubric misread framing; the scripted scenarios, the style sweep and the `--scenario` driver went with it. The project is an MVP of one thing: a film that plays and takes steering, not a research harness.
- **The Showrunner never blocks a segment (2026-09-17).** Its update lands at the start of the first segment that finds the task finished; a running task is left alone. Runs are at least `showrunner_min_gap` segments apart and one at a time; held triggers are noted in the transcript. Before this, a synchronous join at the next segment stalled the pipeline 2-8 s on a fifth of the segments (`docs/reviews/2026-09-17-continuity-and-buffering.md`).
- **The Director writes beats and the loop renders just in time (2026-09-17, implemented after the dry runs, not yet run live).** A `BeatPlan` is one action or state as 1-4 clips (`max_clips_per_beat`), each clip with its own action, end state and entity ops, so the world advances clip by clip and a beat can be cut short after any clip. The engine emits clips with no LLM call; the Director is called when the beat ends or when the viewer speaks (the rest of the beat is dropped, a Director call in flight is cancelled and restarted with the line). The live loop fires each render `render_slack_seconds` plus the estimate before the clip is due on air and asks for the next beat only when needed, listening for the viewer in between; with the renderer under real time a line lands two clips after it is typed. The Director's call size was the per-clip critical path before: about 330 output tokens take 5.8 s on the default tier and 2.1 s on OpenAI's priority tier (`director_service_tier: "priority"` in the live config). Characters carry `posture`, `activity` and `position` in the world model, pacing and scene length are counted in story seconds (`stagnation_seconds`, `scene_seconds`), the Showrunner's notes live in the user prompt so the system prompt stays cached, and the compiler sends the full style and looks only on the opening clip (`visual_anchor` and `look_short` from the blueprint afterwards). All of this needs a live session to verify.
- **The previous four-role engine was deleted on 2026-09-08** after a 30-turn A/B and a 180-segment run (`docs/reviews/`). Its design is documented under `docs/legacy/` for reference only.
