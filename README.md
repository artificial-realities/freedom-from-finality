# Artificial Realities

An interactive generative movie platform: a continuously streaming AI-generated film whose story responds to the viewer while it plays.

## The vision

A movie starts and keeps going. The viewer watches, and while watching can express what they want the story to do — a direction, a turn, an idea. The system reads that as intent and reflects it in what happens next, provided it fits the story's underlying constraints. No pausing, no menus, no branching tree of pre-authored paths. One continuous film that listens.

The goal is not a clip generator with a text box on it. It is a system that can hold a story together across a long runtime while remaining open to influence.

## The problem

Getting there means solving several things at once:

- **Continuous generation** — produce and present video without the stream stopping to think.
- **Continuity over time** — keep story, characters, world, and narrative thread coherent as the runtime grows.
- **Influence** — accept user input and interpret it as story intent rather than as a raw prompt.
- **Translation** — turn that intent into concrete changes in future scenes.
- **Constraints** — preserve higher-level story guidelines while still letting influence matter. Steering should bend the story, not break it.
- **Cinematic cohesion** — connect scenes so the result reads as one film, not a reel of isolated AI clips.

None of these is fully solved here. The prototype is one attempt at attacking them together.

## Current prototype

The prototype generates the film as a sequence of short segments that are stitched into a continuous stream. An LLM layer decides what happens next and how to shoot it; an image-to-video model renders it; the viewer types free-text guidance between clips, and the story keeps moving whether they do or not.

Concretely, as of today:

- **Narrative orchestration** is two LLM roles on two clocks over a typed world model. A **Director** makes one structured call per segment: what happens, how it is shot, what is said, what changed in the world, and how the viewer's input was handled. A **Showrunner** runs off the critical path on triggers to compact the event log into summaries and write the next scene card. Code owns the world model, validation, the prompt compiler, pacing and persistence. This is one mechanism being explored for coordinating generation, not a fixed part of the design; the previous four-role engine is kept for A/B comparison.
- **Video** comes from LTX-2.5 running as a resident inference server on a GPU box. It owns a sliding window: the tail of each generated chunk conditions the next one, which carries visual state forward between continuous shots; a cut re-seeds from the reference image. It also renders a native ambient/SFX track, and narration when the Director writes a line.
- **Playback** muxes and concatenates clips with ffmpeg.
- **Spoken voice-over** can alternatively go through ElevenLabs TTS for a fixed narrator voice; both video and audio generation can be disabled entirely for headless development.

The current test story is deliberately minimal — a single LEGO minifigure alone in a white void, no dialogue, no locations, no other characters. It strips away every variable except long-horizon continuity, which is the hard part.

## Where it stands

Working: end-to-end generation from user input to stitched, narrated video. Open:

- **Identity drift.** Characters do not hold their appearance reliably across continuous clips. Cuts bound it by re-seeding from the reference; observation of rendered frames is the next step.
- **Continuity vs. steering.** The sliding window trades one against the other directly (`ltxv_continuity_strength`). High continuity means new input barely lands; low means the picture jumps. A better mechanism is needed, not a better setting.
- **Latency.** Roughly 170s per clip on an A100-80GB at 15 sampling steps. Far from streaming.

## Repository

`main.py` runs the prototype against a config in `configs/`. `docs/ARCHITECTURE.md` describes the current orchestration; `docs/reviews/` holds the architectural review that motivated it; `docs/legacy/` documents the previous four-role engine; `server/README.md` covers the LTX-2.5 inference server.

```bash
pip install -r requirements.txt
cp .env.example .env

python main.py play --config configs/director.yaml       # headless, no video or audio
python main.py play --config configs/director.live.yaml  # live video (needs the LTX-2.5 server)
python -m tests.run                                      # offline test suite
```
