# CLAUDE.md

Artificial Realities is an interactive generative movie platform — a continuously streaming AI-generated film whose story responds to viewer input while it plays. See [README.md](README.md) for the vision and the open problems.

The current prototype reaches that through a deterministic turn engine that drives four LLM roles on split clocks and renders each beat with LTX-2.5. **The four-role split is one mechanism under exploration, not the definition of the project** — treat it as replaceable.

## Layout

```
main.py                  # play command; --config, --story, --scenario, --log-dir
src/
  graph/mas_graph.py     # TurnEngine — asyncio clock-split orchestration
  agents/                # tolkien, spielberg, attenborough, spock (+ _common)
  prompts/               # system + user template per agent (.md)
  state/story_state.py   # StoryState
  models/                # config, story, responses
  i2v/                   # image-to-video backends (base, ltxv)
  llm/                   # LLM backends (base, openai_backend)
  memory/mem0_store.py   # opt-in episodic memory
  tts/elevenlabs.py      # opt-in voice-over TTS
  playback/mux.py        # ffmpeg concat + audio mux
  eval/runner.py         # run_play / run_live / run_scenario
  ui/terminal.py         # free-text input between clips
  util/                  # json sanitizer, langfuse, media, prompt loader, story log
configs/                 # mas.yaml (headless dev) · mas.live.yaml (LTX-2.5) · mas.mem0.yaml (Mem0)
data/                    # story.json blueprint · demo_scenario.json · legoman.png seed image
server/                  # LTX-2.5 inference server (runs on a GPU box; see server/README.md)
```

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts.
- **Agents**: async functions, not classes.
- **Workflow, not agent system**: no tool use, no LLM-directed control flow. The turn engine decides order; agents only produce content.
- **Rendering is opt-out, content is not.** Every agent produces complete output whether or not video and TTS are enabled, so the system behaves identically to a live run when rendering is off. Never gate content generation on `video_enabled` / `audio_enabled` — gate only rendering.

## Running

```bash
python main.py play --config configs/mas.yaml                                    # headless dev — no video or audio
python main.py play --config configs/mas.yaml --scenario data/demo_scenario.json # non-interactive, scripted input
python main.py play --config configs/mas.live.yaml                               # live video (needs the LTX-2.5 server)
```

Default to the headless config. Live runs need an LTX-2.5 server reachable at `ltxv_server_url` on a GPU box, so they are not available by default in this environment. Runs write a markdown transcript to `logs/`.

## Current constraints

- **Identity drift** across clips is the main open problem. Visual state carries forward only through the server's sliding window.
- **`ltxv_continuity_strength`** trades continuity against steering directly. At `1.0` new prompts barely land; lower values let steering through at the cost of visual continuity.
- **The distilled LoRA is broken** in diffusers ≤0.41 (missing connector modules corrupt output). The server runs the base model at 15 steps, ~170s/clip on A100-80GB.
- **LTX-2.5 renders native ambient audio**, so clips already carry an audio track; ElevenLabs voice-over is mixed over it (ducked), not substituted.
