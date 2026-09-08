# CLAUDE.md

Artificial Realities is an interactive generative movie platform — a continuously streaming AI-generated film whose story responds to viewer input while it plays. See [README.md](README.md) for the vision and the open problems.

The current prototype reaches that through a deterministic engine that drives **two LLM roles on two clocks** (a per-segment Director and a slow-clock Showrunner) over a **typed world model**, and renders each segment with LTX-2.5. The previous four-role engine is kept behind `graph: "mas_graph"` for A/B comparison only. **The orchestration is one mechanism under exploration, not the definition of the project** — treat it as replaceable.

## Layout

```
main.py                  # play command; --config, --story, --scenario, --log-dir
src/
  graph/director_engine.py   # DirectorEngine — current design (graph: "director")
  graph/mas_graph.py         # legacy four-role TurnEngine (graph: "mas_graph")
  agents/director.py         # one structured call per segment (Segment)
  agents/showrunner.py       # slow-clock compaction + planning (ShowrunnerUpdate)
  agents/                    # tolkien, spielberg, attenborough, spock — legacy engine only
  prompts/                   # director.*.md, showrunner.*.md (+ legacy templates)
  story/                     # typed world model (world), schemas, validator, context, compiler, pacing
  state/story_state.py       # StoryState — runner-facing surface shared by both engines
  models/                    # config, story (blueprint), responses (legacy views)
  i2v/                       # image-to-video backends (base, ltxv); `reset` = cut
  llm/                       # LLM backends (openai, mock)
  memory/mem0_store.py       # legacy engine only
  tts/elevenlabs.py          # opt-in voice-over TTS (narration: "tts")
  playback/mux.py            # ffmpeg concat + audio mux
  eval/runner.py             # run_play / run_live / run_scenario
  ui/terminal.py             # free-text input between clips
  util/                      # json sanitizer, langfuse, media, prompt loader, story log
tests/                       # python -m tests.run (no pytest needed)
configs/                     # director.yaml (headless) · director.live.yaml (LTX-2.5) · mas.*.yaml (legacy)
data/                        # story.json blueprint · demo_scenario.json · ab_scenario.json · legoman.png
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

- **Identity drift** across clips is the main open problem. `transition: cut` re-seeds the renderer from the reference image; continuous shots still carry state only through the server's sliding window. Observation of rendered frames is not built yet.
- **`ltxv_continuity_strength`** trades continuity against steering for `continue` shots. At `1.0` new prompts barely land; lower values let steering through at the cost of visual continuity.
- **The distilled LoRA is broken** in diffusers ≤0.41 (missing connector modules corrupt output). The server runs the base model at 15 steps, ~170s/clip on A100-80GB.
- **LTX-2.5 renders native ambient audio**, so clips already carry an audio track; with `narration: "tts"` the ElevenLabs line is mixed over it (ducked), not substituted.
- **The legacy engine is kept only until a long run confirms the new one.** A 30-turn A/B (`docs/reviews/2026-09-08-ab-results.md`) favoured the Director engine on speed, calls, prop ledger and progression; run 100+ segments under `configs/director.yaml` before deleting `graph: "mas_graph"`, Mem0 and the legacy prompts.
