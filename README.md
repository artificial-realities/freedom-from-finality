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

- **Narrative orchestration** is two LLM roles on two clocks over a typed world model. A **Director** makes one structured call per segment: what happens, how it is shot, what is said, what changed in the world, and how the viewer's input was handled. A **Showrunner** runs off the critical path on triggers to compact the event log into summaries and write the next scene card. Code owns the world model, validation, the prompt compiler, pacing and persistence. This is one mechanism being explored for coordinating generation, not a fixed part of the design; it replaced a four-role split after an A/B and a 180-segment run.
- **Video** comes from LTX-2.5 running as a resident inference server on a GPU box. It owns a sliding window: the tail of each generated chunk conditions the next one, which carries visual state forward from clip to clip; only the first clip of a run is seeded from the reference image. It also renders a native ambient/SFX track, and narration when the Director writes a line.
- **Playback** is a web player: the film streams to a browser clip by clip, holds the last frame while the next segment renders, and takes typed input at any moment; the Director reports whether each line was absorbed, deferred or declined. Without a video backend the same page plays the film as text at story pace. Clips are muxed and concatenated with ffmpeg.
- **Spoken voice-over** can alternatively go through ElevenLabs TTS for a fixed narrator voice; both video and audio generation can be disabled entirely for headless development.

The current test story is deliberately minimal — a single LEGO minifigure alone in a white void, no dialogue, no locations, no other characters. It strips away every variable except long-horizon continuity, which is the hard part.

## Where it stands

Working: end-to-end generation from viewer input to a narrated film streaming in the browser, steered while it plays. Open:

- **Latency.** 17–23 s to generate a 6–8 s clip on an H100, about 3× real time, so the player holds the last frame between clips. Seamless playback needs generation at or below real time.
- **Re-seeding resets the look.** Seeding a clip from the reference image reverts anything the story has changed about a character (a cap picked up mid-film), so the film never cuts: every clip continues the previous one and only the first is seeded from the reference. Identity holds through continuous clips on the distilled recipe; a new angle has to be a camera move.
- **Continuity vs. steering.** The sliding window trades one against the other directly (`ltxv_continuity_strength`). High continuity means new input barely lands; low means the picture jumps. A better mechanism is needed, not a better setting.

## Repository

`main.py` runs the prototype against a config in `configs/`. `docs/ARCHITECTURE.md` describes the current orchestration; `docs/reviews/` holds the architectural review that motivated it; `docs/legacy/` documents the previous four-role engine; `server/README.md` covers the LTX-2.5 inference server.

```bash
pip install -r requirements.txt
cp .env.example .env

python main.py play --config configs/director.yaml                     # headless, no video or audio
python main.py play --config configs/director.yaml --player web        # the film as text in a browser, steer while it plays
python main.py play --config configs/director.live.yaml --player web   # live video (needs the LTX-2.5 server)
python main.py play --config configs/director.replay.yaml --player web # the player with replayed clips, no GPU
python -m tests.run                                                    # offline test suite
```
