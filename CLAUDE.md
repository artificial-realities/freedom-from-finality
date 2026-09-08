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
  i2v/                       # image-to-video backends (base, ltxv); `reset` = cut
  llm/                       # LLM backends (openai, mock for offline tests)
  tts/elevenlabs.py          # opt-in voice-over TTS (narration: "tts")
  playback/mux.py            # ffmpeg concat + audio mux
  eval/runner.py             # run_play / run_live / run_scenario
  ui/terminal.py             # free-text input between clips
  util/                      # json sanitizer, langfuse, media, prompt loader, story log
tests/                       # python -m tests.run (no pytest needed)
scripts/style_sweep.py       # style / negative-prompt / continuity sweep against a running server
configs/                     # director.yaml (headless) · director.live.yaml (LTX-2.5)
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
python main.py play --config configs/director.live.yaml                                   # live video (needs the LTX-2.5 server)
python -m tests.run                                                                       # offline test suite (mock LLM)
```

Default to the headless config. Live runs need an LTX-2.5 server reachable at `ltxv_server_url` on a GPU box, so they are not available by default in this environment. Runs write a markdown transcript to `logs/` and the world model to `logs/sessions/<session>/`.

## Current constraints

- **Identity drift** across clips is the main open problem. `transition: cut` re-seeds the renderer from the reference image (first shot of a segment only); continuous shots carry state only through the server's sliding window. The seed image is letterboxed to the render size by the client (a cropped reference made the model invent a new head); what remains is generation-level colour, expression and background drift within clips (`docs/reviews/2026-09-08-live-run.md`). The observation loop (`src/vision/`) checks every rendered clip against the sheets and forces a cut on drift.
- **`ltxv_continuity_strength`** trades continuity against steering for `continue` shots. At `1.0` new prompts barely land; lower values let steering through at the cost of visual continuity.
- **The distilled LoRA is broken** in diffusers ≤0.41 (missing connector modules corrupt output). The server runs the base model at 15 steps, ~200 s per 7 s clip on an A100-80GB with CPU offload. `server/vast_run.sh` rents and tears down a GPU on Vast.ai.
- **LTX-2.5 renders native ambient audio**, so clips already carry an audio track; with `narration: "tts"` the ElevenLabs line is mixed over it (ducked), not substituted.
- **The previous four-role engine was deleted on 2026-09-08** after a 30-turn A/B and a 180-segment run (`docs/reviews/`). Its design is documented under `docs/legacy/` for reference only.
