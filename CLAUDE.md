# CLAUDE.md

Artificial Realities is an interactive generative movie platform — a continuously streaming AI-generated film whose story responds to viewer input while it plays. See [README.md](README.md) for the vision and the open problems.

The current prototype reaches that through a deterministic engine that drives **two LLM roles on two clocks** (a per-segment Director and a slow-clock Showrunner) over a **typed world model**, and renders each segment with LTX-2.5. **The orchestration is one mechanism under exploration, not the definition of the project** — treat it as replaceable.

## Layout

```
main.py                  # play command; --config, --story, --player, --segments, --log-dir
src/
  graph/director_engine.py   # DirectorEngine — current design (graph: "director")
  agents/director.py         # one structured call per segment (Segment)
  agents/showrunner.py       # slow-clock compaction + planning (ShowrunnerUpdate)
  prompts/                   # director.*.md, showrunner.*.md
  story/                     # typed world model (world), schemas, validator, context, compiler, pacing
  state/story_state.py       # StoryState — runner-facing surface shared by both engines
  models/                    # config, story (blueprint), responses (legacy views)
  i2v/                       # image-to-video backends (base, ltxv, replay); `reset` = cut
  llm/                       # LLM backends (openai, mock for offline tests)
  tts/elevenlabs.py          # opt-in voice-over TTS (narration: "tts")
  playback/mux.py            # ffmpeg concat + audio mux
  playback/player.py         # Playable + Player boundary of the live loop; TerminalPlayer (ffplay + stdin)
  web/                       # WebPlayer (aiohttp: SSE stream, /input, clips) + player.html
  eval/runner.py             # run_live (the film) · run_play (terminal, turn by turn)
  ui/terminal.py             # free-text input between clips (run_play)
  util/                      # json sanitizer, langfuse, media, prompt loader, story log
tests/                       # python -m tests.run (no pytest needed)
scripts/steer.py             # scripted viewer: posts lines to a running web player at given segments
configs/                     # director.yaml (headless) · director.live.yaml (LTX-2.5) · director.replay.yaml (replayed clips)
data/                        # story.json blueprint · legoman.png reference image
docs/                        # ARCHITECTURE.md · reviews/ · legacy/
server/                      # LTX-2.5 inference server (runs on a GPU box; see server/README.md)
```

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts. The world model (`src/story/world.py`) is the system of record; prose is never the source of truth.
- **Agents**: async functions, not classes. They read prompt sections from `src/story/context.py` and return schema instances; they never mutate the world. Only `src/story/validator.py` mutates the world.
- **Workflow, not agent system**: no tool use, no LLM-directed control flow. The engine decides order and triggers; the LLM proposes, code validates and applies.
- **Rendering is opt-out, content is not.** The Director produces the voice-over field whenever the gate is open; only its rendering depends on `narration` / `audio_enabled`. Never gate content generation on rendering flags.
- **The video model's dialect lives in one place**: `src/story/compiler.py`. Schemas do not change when the model does.
- **Structured outputs are strict**: keep `Segment` and `ShowrunnerUpdate` inside OpenAI's strict subset (no free-form dicts). Enforce structure in the validator, not the schema.

## Running

```bash
python main.py play --config configs/director.yaml                                        # headless dev — no video or audio
python main.py play --config configs/director.yaml --player web                           # the film as text in a browser; type to steer
python main.py play --config configs/director.live.yaml --player web                      # live video in the browser (needs the LTX-2.5 server)
python main.py play --config configs/director.replay.yaml --player web                    # the player with replayed clips from logs/video, no GPU
python -m tests.run                                                                       # offline test suite (mock LLM)
```

Default to the headless config. Live runs need an LTX-2.5 server reachable at `ltxv_server_url` on a GPU box, so they are not available by default in this environment. `--player web` serves the player at `http://127.0.0.1:8765/` (`--port`, `--host`); `--segments N` bounds a live run. Runs write a markdown transcript to `logs/` and the world model to `logs/sessions/<session>/`.

## Current constraints

- **Within-clip identity drift was the sampling recipe, not the prompts.** Every run before 2026-09-15 ran the distilled transformer with the full-model recipe (CFG 3, STG, 15 linear steps); on the correct distilled recipe (`LTX2_VARIANT=distilled`, 8 sigmas, unguided) the figure's torso, legs and ground hold through the action with no anchor, and the Director continues across segments instead of cutting every one (`docs/reviews/2026-09-15-substrate-sweep.md`). The Director wrote eyebrows, frowns and smirks and the model painted them; the blueprint now forbids facial expressions and the verification run shows plain faces. Real residual drift is a greying ground at the end of the busiest clips. A `cut` re-seeds from the reference image, so it also resets costume and props to the reference's state (the red cap vanished at a cut and came back mid-clip): while the look differs from the reference, cuts need a reference that carries it. `transition: cut` re-seeds the renderer from the letterboxed reference and opens from `reference_pose`. A viewer's line typed while clip N plays lands at N+2: the live loop starts the Director call for N+2 as late as the render of N+1 allows and reads the lines then (`run_live`, late start), where it used to read them when clip N started playing and the line landed at N+3.
- **`ltxv_continuity_strength`** trades continuity against steering for `continue` shots. At `1.0` new prompts barely land; lower values let steering through at the cost of visual continuity.
- **The server picks the weights and the recipe with `LTX2_VARIANT`** (`server/ltx2_server.py`): `distilled` (default) is the 8-sigma unguided recipe on `transformer/`; `full` loads `transformer_full/` at 30 steps with guidance and shifting, and on 2026-09-15 gave the same identity at 3.5× the time with an extra-character artefact, so it is not used. Placement is `LTX2_OFFLOAD`: `gpu` (default) keeps everything resident with the prompt enhancer dropped (70 GB on an H100 PCIe; 12–13 s per 5 s clip and 17–23 s per 6–8 s clip, of which the 8 transformer forwards are 11–19 s, plus 1.2–1.8 s to encode; a fresh box adds 10–20 s of CPU warm-up to its first three clips); `text` offloads only the Gemma text encoder (28 s); `cpu` offloads everything (57 s). The server logs a per-stage breakdown (`timings:`) each turn and encodes the MP4 through ffmpeg (diffusers' PyAV path took 17–24 s per clip). `server/vast_run.sh` rents and tears down a GPU on Vast.ai (`up`, `restart` to switch variant or placement without re-renting, `down`).
- **Identity anchor**: `ltxv_anchor_strength` conditions every clip on the reference image as a guiding keyframe at `ltxv_anchor_index` (latent frames, -1 = last), on top of the index-0 condition. Off: it is not needed for identity on the correct recipe, an end anchor suppresses the closing action and a mid anchor inserts an idle beat.
- **LTX-2.5 renders native ambient audio**, so clips already carry an audio track; with `narration: "tts"` the ElevenLabs line is mixed over it (ducked), not substituted.
- **The frame observer and the evaluation tooling were removed on 2026-09-16.** The observer (a vision check of every rendered clip that showed the Director a "Picture" section and forced a cut on drift) measured a drift the recipe fix had already removed, its forced cut never fired in any run, and its rubric misread framing; the scripted scenarios, the style sweep and the `--scenario` driver went with it. The project is an MVP of one thing: a film that plays and takes steering, not a research harness.
- **The previous four-role engine was deleted on 2026-09-08** after a 30-turn A/B and a 180-segment run (`docs/reviews/`). Its design is documented under `docs/legacy/` for reference only.
