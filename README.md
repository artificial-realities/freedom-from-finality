# Artificial Realities

An interactive generative movie platform: a continuously streaming AI-generated film whose story responds to the viewer while it plays.

## The vision

The experience should feel like watching a continuously unfolding movie guided by an imaginative, responsive storyteller, much like listening to someone tell a story around a campfire. The story should have a life of its own, progressing naturally and remaining engaging even when the viewer does nothing. Characters should have distinct personalities, motivations, relationships, and autonomy, while the world maintains its own internal logic and consistency.

The viewer should be able to influence the story at any moment through natural, unrestricted input, as if making suggestions to the storyteller rather than issuing commands or selecting from predefined choices. These suggestions should be interpreted creatively and integrated organically into the unfolding narrative, without automatically being obeyed or disrupting its coherence. Viewer input should have the potential to create meaningful consequences, both immediate and long-term, while characters remain free to act according to their motivations. The storyteller should balance responsiveness with creative independence, introducing believable surprises, developing conflicts, and maintaining narrative momentum without relying on the viewer to drive every event.

The experience should feel cinematic, immersive, and continuous, with natural transitions, consistent environments, believable dialogue, and coherent visual and audio progression rather than a sequence of disconnected generated clips. Above all, it should feel like a living story that listens to the viewer, remembers what happens, and evolves naturally in ways that neither the viewer nor the storyteller has completely predetermined.

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

- **Narrative orchestration** is two LLM roles on two clocks over a typed world model. A **Director** makes one structured call per beat, one action or state of the film as a few clips: what the camera sees in each clip, what is said, what changes in the world, and how the viewer's input was handled; the clips are rendered one by one with no model call in between, and a viewer's line cuts the beat short. A **Showrunner** runs off the critical path on triggers to compact the event log into summaries and write the next scene card. Code owns the world model, validation, the prompt compiler and persistence; whether the film is moving is the two roles' judgment. This is one mechanism being explored for coordinating generation, not a fixed part of the design; it replaced a four-role split after an A/B and a 180-segment run.
- **Video** comes from LTX-2.5 running as a resident inference server on a GPU box. It owns a sliding window: the tail of each generated chunk conditions the next one, which carries visual state forward from clip to clip; only the first clip of a run is seeded from the reference image. It also renders the sound natively: ambient and foley, and the characters' lines when the Director writes them.
- **Playback** is a web player: the film streams to a browser clip by clip, holds the last frame while the next segment renders, and takes typed input at any moment; the Director reports whether each line was absorbed, deferred or declined. Without a video backend the same page plays the film as text at story pace. The played clips are concatenated into one film with ffmpeg at the end of a run.

The one story is deliberately small: a LEGO minifigure in a white void, one location, who talks, meets a classic LEGO spaceman when a bit first fails badly, and takes visitors from any LEGO theme. It is written in the story blueprint (`stories/lego-void/story.json`: the premise, the question the film keeps open, what keeps happening, what holds whatever the viewer does, and the storyteller's theme, tone, temperament and refusals), and it exercises every feature of the engine while keeping long-horizon continuity, the hard part, in view.

## Where it stands

Working: end-to-end generation from viewer input to a film streaming in the browser, steered while it plays. A 64-clip live film on one H100 played without a stall, every viewer line on screen two clips after it was typed. Open:

- **The Director's latency tail.** A slow call at a beat's end or on a viewer's line can still hold the picture: a 300-clip film had three stalls, all from Director calls of 6-14 s. The current Director (gpt-5.6-luna) has run headless, not yet in a live film with video.
- **Re-seeding resets the look.** Seeding a clip from the reference image reverts anything the story has changed about a character (a cap picked up mid-film), so only the first clip is seeded and every other continues the one before. A new angle has to be a camera move, or a written cut where the story allows one.
- **Continuity against steering.** The sliding window trades one for the other directly (`ltxv_continuity_strength`): high continuity means new input barely lands, low means the picture jumps. A better mechanism is needed, not a better setting.
- **Long films drift.** As props accumulate, the LEGO story's white void turns into a baseplate floor.
- **Unverified on the GPU:** two speakers in one clip, and a minifigure's printed face while it speaks.

Why the design is what it is, with the evidence: [`docs/decisions.md`](docs/decisions.md).

## Performance

One live film of 64 clips on an H100 SXM, client and server on the box: LTX-2.5 distilled (8 sigmas, unguided), fp8, regional `torch.compile`, 512×288 at 24 fps, 6 s clips with the overlap seam; gpt-5.4-mini as Director.

| | |
|---|---|
| Render per 6 s clip | 3.5 s mean, 3.7 s p95 (0.62 of the clip's length) |
| Stalls (the picture held for the next clip) | none |
| Smallest lead of a ready clip over its air time | 2.3 s |
| Viewer line to its clip on air | 9.6-13.4 s (9 lines) |
| First picture | 13.8 s |
| Seams | no duplicate frames, no luminance dip, browser swap p95 6.7 ms |
| LLM cost | $0.50 per film-hour (gpt-5.6-luna for both roles) |

`scripts/latency_probe.py` checks whether a server setting renders in real time. Every run writes `logs/sessions/<id>/timing.jsonl` (fires, renders, queue margins, stalls, viewer lines, Director calls) next to its world model and transcript.

## Repository

`main.py` runs the prototype against a config in `configs/`. `docs/ARCHITECTURE.md` describes the current orchestration and `docs/decisions.md` why it is built that way; `stories/README.md` is the field guide to a story blueprint; `server/README.md` covers the LTX-2.5 inference server.

It needs Python 3.10 or newer, ffmpeg (`ffmpeg` and `ffprobe`; `ffplay` for the terminal player) and an OpenAI API key; live video also needs an 80 GB GPU running the LTX-2.5 server.

```bash
pip install -r requirements.txt
cp .env.example .env                                                   # OPENAI_API_KEY

python main.py play --config configs/director.yaml                     # headless, no video or audio
python main.py play --config configs/director.yaml --player web        # the film as text in a browser, steer while it plays
python main.py play --config configs/director.live.yaml --player web   # live video (needs the LTX-2.5 server)
pytest                                                                 # offline test suite (mock LLM, no network)
```
