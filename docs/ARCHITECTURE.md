# Architecture

Freedom from Finality generates an infinite story as a sequence of 6-second clips while a viewer watches and steers. Why it is built this way, with the evidence, is in [decisions.md](decisions.md).

## In one paragraph

Two LLM roles on two clocks; everything else is deterministic code. **Now** makes one structured call per **beat** (one action or state of the story, one to four clips) and returns everything the beat needs: each clip's picture, its dialogue, the world changes that happen in it, and how the viewer's line was taken. The engine then emits the beat's clips one per segment with no LLM call in between, and asks Now again when the beat has played out, or at once when the viewer speaks. **Then** runs off the critical path: it compacts the event log into summaries, rewrites the notebook (characters, seeds, intentions) and its understanding of the viewer, notes the viewer's marks on the story, and may recognise an era, and nothing ever waits for it. Code owns the rest: the typed world model (the only source of truth), the validator that decides what Now may change, the context assembler that decides what goes into each prompt, the compiler that turns a clip into the video model's prompt, and the live loop that renders each clip just in time.

```
viewer input ─▶ Engine (deterministic, asyncio)
                     │ assemble context                          │ trigger: every N segments
                     ▼                                           ▼
        Context assembler (code)                            Then (LLM, slow clock)
        notebook · characters, their looks and state        summaries · notebook rewrite
        · recent clips · the viewer's lines                  · era recognition
                     │                                       
                     ▼                    ◀── lands at the next plan ──
        Now (LLM, once per beat)
        clips (action · end state · world ops) · dialogue · viewer disposition
                     │ proposes            ◀── a hard violation: one re-ask
                     ▼
        Validator (code): entities, speakers, cuts, per clip
                     │ applies one clip per segment
                     ▼
        World model (typed, saved per clip)
                     │ clip
                     ▼
        Prompt compiler (code) ─▶ LTX-2.5 server (sliding window, native audio) ─▶ web player
```

| Component | File | Responsibility |
|---|---|---|
| Live loop | `src/runner.py` | Producer (plan, emit, render just in time, interrupt, roll back) and consumer (clips → player, commit on air, delete played clips) |
| Adventure | `src/adventure.py` | The viewer's one adventure on disk: begin once, resume, commit as of each aired clip, crash recovery, adopt a copy brought home |
| Engine | `src/engine.py` | Plan a beat, emit its clips, cut it short, the Then clock, the transcript notes |
| Now | `src/agents/now.py`, `src/prompts/now.*.md` | One `BeatPlan` per beat; one re-ask on hard violations |
| Then | `src/agents/then.py`, `src/prompts/then.*.md` | One `ThenUpdate` per trigger: memory and intuition |
| World model | `src/story/world.py` | One timeline: entities, interventions, the viewer's direction, notebook, event log, summaries; save |
| Viewer's memory | `src/story/viewer.py` | The person, outside every timeline: their lines, what the story did with them, Then's understanding of them, their marks; append-only |
| Validator | `src/story/validator.py` | `check` (the whole beat) and `apply_clip` (one clip); the only code that mutates the world |
| Context | `src/story/context.py` | Every prompt section, so the prompt budget is decided in one place |
| Compiler | `src/story/compiler.py` | One clip → LTX-2.5 prompt text; the only code that knows the video model's dialect |
| Renderer | `src/i2v/ltxv.py`, `server/ltx2_server.py` | The HTTP client and the resident LTX-2.5 server |
| Player | `src/web/` | The page and its server: the story, and a box to type into |

## A beat, then its clips

**Plan** (one Now call). A finished Then run lands first; one still running is left alone, so the fast clock never pays for the slow one. The viewer's line, if any, is recorded as an intervention. On the first segment Then runs once synchronously, before anything is on air, to write the opening notebook from the frame zero's first frame and its character. Now then gets a cached system prompt (its craft, the content policy and the output contract, the same in every adventure) and a user prompt with the notebook, the world as it is now (every character in the scene with its look), the viewer's lines, summaries, the recent clips verbatim, and where the picture is (on the first beat, the frame zero's first frame). It returns a `BeatPlan`. A hard violation anywhere in the beat (an unknown entity, a new character without a look, a line spoken by a prop) earns exactly one re-ask with the violations spelled out. Then is triggered at the bootstrap and every `compaction_interval` segments. At most one run is in flight, and runs are at least `then_min_gap` segments apart.

**Emit** (one clip per segment, no LLM). `apply_clip` applies that clip's world changes, so a beat cut short leaves the world exactly as far along as the picture. The compiler turns the clip into one flowing prompt: sound, framing and camera, who is on screen, who enters, the action, the dialogue as `Name says in a … voice, "line"`, and the image the clip ends on. The first clip carries every character's full look and opens on the frame zero's first frame; later clips carry short looks, and anything entering the picture is described on the clip it enters. There is no style paragraph: how anything is drawn is in its look, so the picture's style changes when looks do. A location with a look is named the same way: in full on the first clip and on the clip the picture arrives there, by its short look while it stays. The event record is appended; when the clip goes on air, the adventure is saved as of it (see below).

**Cut short.** When the viewer speaks while a beat still has clips to emit, they are dropped, a Now call in flight is cancelled and restarted with the line, and the next plan is told how the beat ended and on which image.

## The world model

Entities (characters, props, locations) carry what code reads in structured fields (kind, present, gone, location) and what only the writers read in prose: each entity has one `state` sentence (a character's pose, place and doing; a prop's place, condition and who has it), replaced whole by the op that changes it. The camera is cinematically free (`camera_location`): when it moves to another location, what is in the picture (or new, or moved by an op) comes along, and everything else stays behind, listed for the writers where and when it was last seen. It goes where something in the picture goes: a character that leaves the picture for another place, or is already out of it, goes there alone. A cut that no op moves lands where its shot evidently is, when nothing in it is from here: the known place it names, or the one place everything it shows was last (going back to a character left somewhere). Characters, props and locations carry a `look` and a `look_short` (restated on every clip they are in), which an op replaces whole when the appearance changes for good (a helmet put on or taken off, a forest under snow). Characters also carry who they are: a `personality` (who it is at heart, from the frame zero or Now's introduce, rewritten only by Then) and `lately` (how it has been behaving, as Then last wrote it), which stay on the character when the notebook drops it; and a `voice`, which an op may give later. Then rewrites a personality only with a `because` (what in the story changed it), which the roles see beside it. Characters not in the scene are one line each, except those the story is pointing at: left where the picture is, or named (or their place named) by the viewer, the notebook, a character in the scene or the recent clips. Those are shown in full, with the summary of the stretch they were last seen in, so a return is written knowing who comes back. Then maintains a **notebook** that Now reads at every beat: a portrait of each character who matters now (its personality, which Then leaves empty to keep, and how it has been lately), what is alive, seeds (up to 8) and loose intentions. Interventions are kept for the whole run: a request Now defers stays visible as owed until a later beat delivers it by name. The event log is append-only, one record per clip; only the last `recent_clips` reach Now verbatim, the rest as summaries.

The **frame zero** (`frame_zero.json` in the project root) is the first frame a new adventure begins from, read only then and copied into it, and nothing more: what it shows (`scene`), the one `character` in it (`name`, `look`, `personality`, `voice`, `state`: what Now gives a character it introduces), the `location` it stands in (`name`, `look`), and the `reference_image` the first clip is rendered from (only a video run needs it; exactly 16:9). An unknown key is an error. All of it becomes ordinary world state: the place can be left, the character's look and state are Now's to change, and its personality is where Then's portrait starts, as for any character. There is no protagonist, no arc, no time axis, and no permanent style. What holds in every adventure is the **content policy** (`src/prompts/policy.md`), in both roles' system prompts.

## The viewer's memory

The world model is one timeline; the viewer's memory (`src/story/viewer.py`) is the person watching, and it is never rolled back with the world or copied with a timeline. It is append-only, `viewer.jsonl`, and every record carries the timeline (`root` until branches exist) and segment it happened at. Code writes the facts: each line as it arrives, with the clip on air then, what that clip showed and who was in it; what the story did with it (absorbed, deferred, declined, later delivered), recorded when the clip that did it reaches the screen, so a clip rolled back never counts; and when a direction ended. Then writes the interpretation: an understanding of the viewer (a few readings, each citing the lines it rests on, which code checks and counts; what they lean toward lately; what is unknown) and their marks, the names, decisions, relationships and changes that came from them, each citing its lines. Nothing about attention is recorded: silence stays unknown.

All of it is kept, little of it is sent. Now reads Then's understanding with each reading's evidence counted, the active direction, and the marks that touch the moment (an entity in the scene, recalled or named) plus the most recent. Then reads its last understanding, the lines new since its last run (or newly answered) in full, earlier lines its readings cite or the moment touches, the marks that touch the moment, and how many more are kept. Retrieval is name matching, the same as recalling absent characters; there are no scores or embeddings.

## The adventure

Each viewer has one adventure, and it never ends: the first `play` begins it from the frame zero, every later one resumes it. It is one portable folder (`adventure/` by default, git-ignored) that a copy backs up or moves to another computer:

```
adventure.json            format, id, kind (video; text only for a test adventure), current timeline
origin/                   the frame zero it began from, copied once
viewer.jsonl              the viewer's memory
timelines/root/
  timeline.json           parent and fork point (none: the only timeline so far)
  events.jsonl            every clip the viewer saw: the story's full history
  continuations/<segment>/
    world.json            the world as of that clip
    window.mp4            the frames the next clip continues from
```

**Saved as of the last clip the viewer saw.** When a clip goes on air its continuation is written under `<segment>.tmp/`, synced and renamed into place (the commit), its event is appended and the previous continuation removed; then the viewer-memory records of that clip are written. The world committed is the one serialised right after the clip was emitted, so it holds nothing the producer prepared later; whatever was emitted but not yet seen is never saved. Every write is durable: append-only lines are synced and each starts on a line of its own, whole files are renamed into place.

**What continues the picture.** LTX-2.5 conditions each clip on exactly one thing, the previous clip's last 9 frames (8k+1 frames, two latent frames; no audio or other state carries over), so that is all a continuation keeps: the last 9 frames of the aired clip, cut losslessly into a 112-275 KB file that decodes to exactly the frames a server restart would restore from. The clip itself is deleted once the next one has replaced it on screen. A clip whose frames cannot be cut keeps the previous ones.

**Resuming.** Opening takes the lock (one process per adventure; a lock left by a dead process or another machine is replaced), removes unfinished commits, loads the newest complete continuation (setting aside one that does not load), makes the event log agree with it and loads the viewer's memory, skipping a line a crash cut off. Now is told where the picture ends, and the lines the viewer typed after the story last answered them become owed suggestions. In memory the world keeps only the recent events the prompts read; the event log keeps them all. A crash anywhere costs at most the clips prepared but not yet seen, and a Then run in flight, which runs again.

**On a rented GPU.** The film runs on the box; `server/vast_run.sh` copies the adventure there first, brings it home every minute and when the film stops, and before `down` destroys the box. A copy brought home replaces the viewer's only if it is the same adventure and not behind (`main.py adopt`), with the replaced one kept as `adventure.previous`; an adoption cut short is undone on the next start rather than beginning a new adventure.

**Checkpoints, later.** A checkpoint will be a continuation that is kept, and a branch a new timeline that starts from one, with its own events after the fork; the viewer's memory already stamps every record with its timeline.

## Who decides what

- **The viewer outranks the notebook.** A line is a suggestion to the storyteller, not a command: it changes what happens from the beat it lands in, in the story's and the characters' own way (a twist, a character who resists, a consequence later). Now reports each line as `absorbed`, `deferred` or `declined` (only what the content policy rules out, answered in a form it allows). A line the story takes up becomes its direction, shown to Now with its age, what was on screen when it was given and how Now took it, until Now says it is fulfilled or has run its course, or a newer line takes its place; meanwhile the notebook's intentions give way.
- **Characters want things; the story progresses.** Each character acts in its own nature, coloured by what it wants, and both roles are asked to keep the story moving.
- **Personality holds; behavior moves.** Now acts each character from its personality and the moment; a single episode is behavior, and Then rewrites a personality only when the story has shown it change.
- **Nobody is the protagonist.** The first character is a character like any other. The story follows whoever it is most alive around; Then notices when attention shifts, Now moves the camera, and a character left behind stays in the world where it was.
- **Pacing is the roles' judgment.** No code measures stagnation. Now ends a moment that keeps coming round, and Then judges the story's rhythm on every compaction.

## The live loop

A producer and a consumer on a small queue. The consumer hands rendered clips to the player, which holds each for its length: **the server is the clock**, so the film runs at story pace whether or not anyone watches, and a browser that joins late seeks into what is on air.

The producer renders **just in time**. It builds a lead first (`startup_clips` + 1 clips), then fires each render its estimated render time plus `buffer_seconds` (4.7 s) before the clip is due on air, and asks Now for the next beat as soon as the current one has no clip left. Until the fire it listens: a viewer's line cuts the beat short and Now writes the next beat from it at once, so a line typed during clip N reaches the screen at clip N+2. A beat that answers a line has at most `answer_max_clips` (2) clips, since that call is on the critical path and its latency tracks its length. Now's latency tail is the main remaining stall risk.

**Failures fail soft.** A failed render is retried once if the retry can still land in time; otherwise the world and the beat are restored to before the clip and the segment is replanned from the clip before, its viewer line answered again. An exception while emitting takes the same route. Three failures in a row stop the story.

**Records.** Every run writes technical records the adventure never needs: the transcript (`logs/<session>.md`), the models' raw outputs (`plans.jsonl`, `then.jsonl`) and a timing log (`timing.jsonl`: fires, renders, queue margins, stalls, viewer lines, Now calls, commits) under `logs/sessions/<session>/`. Rendered clips wait in its `clips/` until they have played, and are then deleted: the film is never kept or replayed.

## Picture and sound

The LTX-2.5 server (`server/ltx2_server.py`) holds the distilled model resident on an 80 GB GPU with fp8 matmuls and compiled transformer blocks, at 512×288. It owns a **sliding window**: every clip is conditioned on the last 9 frames of the one before, and only a film's first clip is seeded from the frame zero's reference image (which must be at the render aspect, 16:9, to the pixel). Nothing re-seeds mid-film, because a re-seed resets costume, props and position to the reference. A client run's first request starts a session on the server from either the reference image (a new adventure) or the frames saved with the last clip (a resume), and nothing an earlier session held carries over; after a server restart the client starts it again from its copy of the last clip's tail. `ltxv_continuity_strength` trades continuity against how strongly a new prompt lands.

**One clip shape.** `clip_seconds: 6` becomes the run's one shape: 153 frames rendered, of which the first 9 repeat the previous clip's tail and 144 (6.0 s) are new. Now is told its length, the engine and story clock count it, the render request carries it and the player holds it. Under fp8 every frame count compiles for minutes, so the server warms the shape at startup and the client refuses a server that has not.

**Seams.** The page keeps three video elements: it cuts the picture to the next clip where that clip's repeated head begins, so every frame is seen once, and crossfades the sound over those 0.375 s with equal power (a clip with speech comes in at once). The server holds every clip under -18 LUFS with its audio padded to the video.

**Sound.** LTX-2.5 renders all of it from the compiled prompt: ambient sound and the characters' lines, in each character's `voice`. There is no narrator and no text-to-speech. Two speakers in one clip render in two distinct voices (checked once on the GPU, by ear).

**Cuts, sparingly.** Now may write "The view cuts to …", which LTX-2.5 follows: never on the first clip, at most one per clip. Cuts land late in the clip and can repaint faces in close-ups, so Now is told to cut only to another place or for a reveal; a walk keeps drawing the old place, so a new place is reached by cutting to it.

## Not built yet

Per-character reference images; retrieval over the event log; relationship graphs; multi-viewer input; checkpoints and branching timelines (the adventure's layout and every record already carry their timeline). One story with several locations has played on the GPU: a cut lands the new place, a walk does not.
