# Architecture

Untitled Project generates an infinite story as a sequence of 6-second clips while a viewer watches and steers. Why it is built this way, with the evidence, is in [decisions.md](decisions.md).

## In one paragraph

Two LLM roles on two clocks; everything else is deterministic code. **Spark** makes one structured call per **beat** (one action or state of the story, one to four clips) and returns everything the beat needs: each clip's picture, its dialogue, the world changes that happen in it, and how the viewer's line was taken. The engine then emits the beat's clips one per segment with no LLM call in between, and asks Spark again when the beat has played out, or at once when the viewer speaks. **Echo** runs off the critical path: it compacts the event log into summaries, rewrites the notebook (characters, seeds, intentions, audience), and may recognise an era, and nothing ever waits for it. Code owns the rest: the typed world model (the only source of truth), the validator that decides what Spark may change, the context assembler that decides what goes into each prompt, the compiler that turns a clip into the video model's prompt, and the live loop that renders each clip just in time.

```
viewer input ─▶ Engine (deterministic, asyncio)
                     │ assemble context                          │ trigger: every N segments
                     ▼                                           ▼
        Context assembler (code)                            Echo (LLM, slow clock)
        frame zero · notebook · characters and their state   summaries · notebook rewrite
        · recent clips · the viewer's lines                  · era recognition
                     │                                       
                     ▼                    ◀── lands at the next plan ──
        Spark (LLM, once per beat)
        clips (action · end state · world ops) · dialogue · viewer disposition
                     │ proposes            ◀── a hard violation: one re-ask
                     ▼
        Validator (code): cast, entities, speakers, cuts, per clip
                     │ applies one clip per segment
                     ▼
        World model (typed, saved per clip)
                     │ clip
                     ▼
        Prompt compiler (code) ─▶ LTX-2.5 server (sliding window, native audio) ─▶ web player
```

| Component | File | Responsibility |
|---|---|---|
| Live loop | `src/runner.py` | Producer (plan, emit, render just in time, interrupt, roll back) and consumer (clips → player); saves the film |
| Engine | `src/engine.py` | Plan a beat, emit its clips, cut it short, the Echo clock, the transcript notes |
| Spark | `src/agents/spark.py`, `src/prompts/spark.*.md` | One `BeatPlan` per beat; one re-ask on hard violations |
| Echo | `src/agents/echo.py`, `src/prompts/echo.*.md` | One `EchoUpdate` per trigger: memory and intuition |
| World model | `src/story/world.py` | Entities, interventions, notebook, event log, summaries; save |
| Validator | `src/story/validator.py` | `check` (the whole beat) and `apply_clip` (one clip); the only code that mutates the world |
| Context | `src/story/context.py` | Every prompt section, so the prompt budget is decided in one place |
| Compiler | `src/story/compiler.py` | One clip → LTX-2.5 prompt text; the only code that knows the video model's dialect |
| Renderer | `src/i2v/ltxv.py`, `server/ltx2_server.py` | The HTTP client and the resident LTX-2.5 server |
| Player | `src/web/` | The page and its server: the story, and a box to type into |

## A beat, then its clips

**Plan** (one Spark call). A finished Echo run lands first; one still running is left alone, so the fast clock never pays for the slow one. The viewer's line, if any, is recorded as an intervention. On the first segment Echo runs once synchronously, before anything is on air, to write the opening notebook from the frame zero. Spark then gets a cached system prompt (the story, its truths, the picture, the cast with looks and voices) and a user prompt with the notebook, the world as it is now, the viewer's lines, summaries, the recent clips verbatim, and where the picture is. It returns a `BeatPlan`. A hard violation anywhere in the beat (an unknown entity, a character the cast does not allow, a line spoken by a prop) earns exactly one re-ask with the violations spelled out. Echo is triggered at the bootstrap and every `compaction_interval` segments. At most one run is in flight, and runs are at least `echo_min_gap` segments apart.

**Emit** (one clip per segment, no LLM). `apply_clip` applies that clip's world changes, so a beat cut short leaves the world exactly as far along as the picture. The compiler turns the clip into one flowing prompt: sound, framing and camera, who is on screen, who enters, the action, the dialogue as `Name says in a … voice, "line"`, and the image the clip ends on. The first clip carries the full style and every character's full look and opens on what the reference image shows; later clips carry a one-sentence anchor and short looks, and anything entering the picture is described on the clip it enters. A location with a look is named the same way: in full on the first clip and on the clip the picture arrives there, by its short look while it stays. The event record is appended and the world saved to `logs/sessions/<id>/`.

**Cut short.** When the viewer speaks while a beat still has clips to emit, they are dropped, a Spark call in flight is cancelled and restarted with the line, and the next plan is told how the beat ended and on which image.

## The world model

Entities (characters, props, locations) carry what code reads in structured fields (kind, present, gone, location) and what only the writers read in prose: each entity has one `state` sentence (a character's pose, place and doing; a prop's place, condition and who has it), replaced whole by the op that changes it. The camera is cinematically free (`camera_location`): when it moves to another location, what is in the picture (or new, or moved by an op) comes along, and everything else stays behind, listed for the writers where it was left. Characters and props carry a `look` and a `look_short` (restated on every clip they are in), which an op replaces whole when the appearance changes for good (a helmet put on or taken off); characters also carry `who`, `wants`, `nature` and `voice`. Echo maintains a **notebook** that Spark reads at every beat: character portraits, what is alive, seeds (up to 8), loose intentions and audience intuition. Interventions are kept for the whole run: a request Spark defers stays visible as owed until a later beat delivers it by name. The event log is append-only, one record per clip; only the last `recent_clips` reach Spark verbatim, the rest as summaries.

The **frame zero** (`stories/<story>/frame_zero.json`) is the minimal starting configuration, not a script: the story's `name`, its `opening`, what is always true (`truths`), who is in it (`characters`, `locations`), how it looks (`picture`), and what kind of `newcomers` may appear. There is no protagonist, no arc, no time axis: the story grows from the frame zero, composed live by Spark and Echo.

## Who decides what

- **The viewer outranks the notebook.** A line is a suggestion to the storyteller, not a command: it changes what happens from the beat it lands in, in the story's and the characters' own way (a twist, a character who resists, a consequence later). Spark reports each line as `absorbed`, `deferred` or `declined` (only when it breaks the world's rules). While a direction is active (or until the next line) the notebook's intentions give way.
- **Characters want things; the story progresses.** Each character acts in its own nature, coloured by what it wants, and both roles are asked to keep the story moving.
- **Pacing is the roles' judgment.** No code measures stagnation. Spark ends a moment that keeps coming round, and Echo judges the story's rhythm on every compaction.

## The live loop

A producer and a consumer on a small queue. The consumer hands rendered clips to the player, which holds each for its length: **the server is the clock**, so the film runs at story pace whether or not anyone watches, and a browser that joins late seeks into what is on air.

The producer renders **just in time**. It builds a lead first (`startup_clips` + 1 clips), then fires each render its estimated render time plus `buffer_seconds` (4.7 s) before the clip is due on air, and asks Spark for the next beat as soon as the current one has no clip left. Until the fire it listens: a viewer's line cuts the beat short and Spark writes the next beat from it at once, so a line typed during clip N reaches the screen at clip N+2. A beat that answers a line has at most `answer_max_clips` (2) clips, since that call is on the critical path and its latency tracks its length. Spark's latency tail is the main remaining stall risk.

**Failures fail soft.** A failed render is retried once if the retry can still land in time; otherwise the world and the beat are restored to before the clip and the segment is replanned from the clip before, its viewer line answered again. An exception while emitting takes the same route. Three failures in a row stop the story.

**Records.** Every run writes the transcript (`logs/<session>.md`), the world model and event log, the models' raw outputs (`plans.jsonl`, `echo.jsonl`), and a timing log (`timing.jsonl`: fires, renders, queue margins, stalls, viewer lines, Spark calls) under `logs/sessions/<session>/`. In video mode the played clips are joined into `logs/video/<session>.mp4` the way the page joins them.

## Picture and sound

The LTX-2.5 server (`server/ltx2_server.py`) holds the distilled model resident on an 80 GB GPU with fp8 matmuls and compiled transformer blocks, at 512×288. It owns a **sliding window**: every clip is conditioned on the last 9 frames of the one before, and only a film's first clip is seeded from the story's reference image (which must be at the render aspect, 16:9, to the pixel). Nothing re-seeds mid-film, because a re-seed resets costume, props and position to the reference. After a server restart the client sends its copy of the last clip's tail and the picture continues. `ltxv_continuity_strength` trades continuity against how strongly a new prompt lands.

**One clip shape.** `clip_seconds: 6` becomes the run's one shape: 153 frames rendered, of which the first 9 repeat the previous clip's tail and 144 (6.0 s) are new. Spark is told its length, the engine and story clock count it, the render request carries it and the player holds it. Under fp8 every frame count compiles for minutes, so the server warms the shape at startup and the client refuses a server that has not.

**Seams.** The page keeps three video elements: it cuts the picture to the next clip where that clip's repeated head begins, so every frame is seen once, and crossfades the sound over those 0.375 s with equal power (a clip with speech comes in at once). The server holds every clip under -18 LUFS with its audio padded to the video.

**Sound.** LTX-2.5 renders all of it from the compiled prompt: ambient sound and the characters' lines, in each character's `voice`. There is no narrator and no text-to-speech. Two speakers in one clip render in two distinct voices (checked once on the GPU, by ear).

**Cuts are the story's choice.** A frame zero may allow cuts (`picture.cuts`); Spark then writes "The view cuts to …", which LTX-2.5 follows. The LEGO story is one continuous take: cuts land late in the clip and repaint faces in close-ups.

## Not built yet

Per-character reference images; retrieval over the event log; relationship graphs; multi-viewer input; resuming a run (every run starts a fresh world, and what it writes is for reading back). One story with several locations has played on the GPU (`stories/lego-quest`, once): a cut lands the new place, a walk does not.
