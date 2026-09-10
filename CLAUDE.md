# CLAUDE.md

Artificial Realities is an interactive generative movie platform — a continuously streaming AI-generated film whose story responds to viewer input while it plays. See [README.md](README.md) for the vision and the open problems.

The current prototype reaches that through a deterministic engine that drives **two LLM roles on two clocks** (a per-segment Director and a slow-clock Showrunner) over a **typed world model**, and renders each segment with LTX-2.5. **The orchestration is one mechanism under exploration, not the definition of the project** — treat it as replaceable.

## Layout

```
main.py                  # play command; --config, --story, --scenario, --log-dir
src/
  graph/director_engine.py   # DirectorEngine — current design (graph: "director")
  agents/director.py         # one structured call per segment (Segment)
  agents/showrunner.py       # slow-clock compaction + planning (ShowrunnerUpdate)
  prompts/                   # director.*.md, showrunner.*.md
  story/                     # typed world model (world), schemas, validator, context, compiler, pacing
  vision/observe.py          # frame observation → visual memory → Picture section + forced cuts
  state/story_state.py       # StoryState — runner-facing surface shared by both engines
  models/                    # config, story (blueprint), responses (legacy views)
  i2v/                       # image-to-video backends (base, ltxv, replay); `reset` = cut
  llm/                       # LLM backends (openai, mock for offline tests)
  tts/elevenlabs.py          # opt-in voice-over TTS (narration: "tts")
  playback/mux.py            # ffmpeg concat + audio mux
  playback/player.py         # Playable + Player boundary of the live loop; TerminalPlayer (ffplay + stdin)
  web/                       # WebPlayer (aiohttp: SSE stream, /input, clips) + player.html
  eval/runner.py             # run_play / run_live / run_scenario
  ui/terminal.py             # free-text input between clips (run_play)
  util/                      # json sanitizer, langfuse, media, prompt loader, story log
tests/                       # python -m tests.run (no pytest needed)
scripts/style_sweep.py       # style / negative-prompt / continuity sweep against a running server
configs/                     # director.yaml (headless) · director.live.yaml (LTX-2.5) · director.replay.yaml (replayed clips)
data/                        # story.json blueprint · scenarios (demo, ab, benchmark) · legoman.png
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
python main.py play --config configs/director.yaml --scenario data/demo_scenario.json     # non-interactive, scripted input
python main.py play --config configs/director.yaml --player web                           # the film as text in a browser; type to steer
python main.py play --config configs/director.live.yaml --player web                      # live video in the browser (needs the LTX-2.5 server)
python main.py play --config configs/director.replay.yaml --player web                    # the player with replayed clips from logs/video, no GPU
python -m tests.run                                                                       # offline test suite (mock LLM)
```

Default to the headless config. Live runs need an LTX-2.5 server reachable at `ltxv_server_url` on a GPU box, so they are not available by default in this environment. `--player web` serves the player at `http://127.0.0.1:8765/` (`--port`, `--host`); `--segments N` bounds a live run. Runs write a markdown transcript to `logs/` and the world model to `logs/sessions/<session>/`.

## Current constraints

- **Identity drift** is the main open problem, and it is now within-clip, not across cuts. `transition: cut` re-seeds the renderer from the letterboxed reference and opens from `reference_pose`; the 2026-09-09 full-combination run showed every cut opening correctly and every clip drifting within ~3 s of action (printed face and torso, grey textured ground, glow), regardless of style text, negative prompt or sheet (`docs/reviews/2026-09-09-full-combination.md`). Prompt-level fixes are exhausted; the next lever is the substrate (reference conditioning through the clip, shorter clips, the distilled model). The observation loop (`src/vision/`) checks every rendered clip against the sheets; under pipelining its result lands two segments later. Because every clip drifts, the Director cuts every segment, so action continuity across segments does not exist yet.
- **`ltxv_continuity_strength`** trades continuity against steering for `continue` shots. At `1.0` new prompts barely land; lower values let steering through at the cost of visual continuity.
- **The server picks the weights and the recipe with `LTX2_VARIANT`** (`server/ltx2_server.py`). `Lightricks/LTX-2.5-Diffusers` loads the *distilled* transformer by default; until 2026-09-09 every run used those weights with the full-model recipe (15 linear steps, CFG 3, STG), which the model card warns costs quality, and the "broken distilled LoRA" was that LoRA stacked on already-distilled weights. `distilled` (default) is the 8-sigma unguided recipe; `full` loads `transformer_full/` at 30 steps with shifting. Neither has been rendered yet with the correct recipe; that is the next GPU session (`scripts/style_sweep.py`, then a live run). Pre-fix latency: ~200 s per 7 s clip on an A100-80GB, 127–155 s on an H100 PCIe, with CPU offload. `server/vast_run.sh` rents and tears down a GPU on Vast.ai.
- **Identity anchor**: `ltxv_anchor_strength` conditions every clip on the reference image as a guiding keyframe at `ltxv_anchor_index` (latent frames, -1 = last), on top of the index-0 condition. Off until the sweep picks a value.
- **LTX-2.5 renders native ambient audio**, so clips already carry an audio track; with `narration: "tts"` the ElevenLabs line is mixed over it (ducked), not substituted.
- **The previous four-role engine was deleted on 2026-09-08** after a 30-turn A/B and a 180-segment run (`docs/reviews/`). Its design is documented under `docs/legacy/` for reference only.
