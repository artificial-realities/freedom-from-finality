# Architecture

Freedom from Finality generates an infinite story as a sequence of 6-second clips while a viewer watches and steers. Why it is built this way, with the evidence, is in [decisions.md](decisions.md).

## In one paragraph

Two LLM roles on two clocks; everything else is deterministic code. **Now** makes one structured call per **beat** (one action or state of the story, one to four clips) and returns everything the beat needs: each clip's picture, its dialogue, the world changes that happen in it, and how the viewer's input was taken. The engine then emits the beat's clips one at a time with no LLM call in between, and asks Now again when the beat's last clip has been emitted, or at once when the viewer types. **Then** runs off the critical path: it summarises the clips since its last run, keeps the story since the last checkpoint and the story so far, rewrites the notebook (characters, seeds, intentions) and its understanding of the viewer, notes the viewer's marks on the story, and may name a checkpoint where the story turned; nothing ever waits for it. Code owns the rest: the typed world model (the only source of truth), the validator that decides what Now may change, the context assembler that decides what goes into each prompt, the compiler that turns a clip into the video model's prompt, and the live loop that renders each clip just in time.

```
viewer input ─▶ Engine (deterministic, asyncio)
                     │ assemble context                          │ trigger: every N clips
                     ▼                                           ▼
        Context assembler (code)                            Then (LLM, slow clock)
        notebook · characters, their looks and state        summaries · notebook rewrite
        · recent clips · the viewer's inputs                 · checkpoints
                     │
                     ▼                    ◀── lands at the next plan ──
        Now (LLM, once per beat)
        clips (action · end state · world ops) · dialogue · how the input was taken
                     │ proposes            ◀── a hard violation: one re-ask
                     ▼
        Validator (code): entities, speakers, cuts, per clip
                     │ applies one clip at a time
                     ▼
        World model (typed, saved per clip)
                     │ clip
                     ▼
        Prompt compiler (code) ─▶ LTX-2.5 server (sliding window, native audio) ─▶ web player
```

| Component | File | Responsibility |
|---|---|---|
| Live loop | `src/runner.py` | Producer (plan, emit, render just in time, interrupt, roll back) and consumer (clips → player, commit on air, delete played clips) |
| Story folder | `src/story_folder.py` | The viewer's one story on disk: begin once, resume, commit as of each aired clip, crash recovery, adopt a copy brought home |
| Engine | `src/engine.py` | Plan a beat, emit its clips, cut it short, the Then clock, the transcript notes |
| Now | `src/agents/now.py`, `src/prompts/now.*.md` | One `BeatPlan` per beat; one re-ask on hard violations |
| Then | `src/agents/then.py`, `src/prompts/then.*.md` | One `ThenUpdate` per run: memory and intuition |
| World model | `src/story/world.py` | Entities, the inputs the story has not finished with, the viewer's direction, notebook, recent clips, the story so far and since the last checkpoint |
| History | `src/story/history.py` | The story's long memory: every summary and checkpoint Then wrote, with the entities each involves; append-only |
| Viewer's memory | `src/story/viewer.py` | The person: their inputs, what the story did with them, Then's understanding of them, their marks; append-only |
| Validator | `src/story/validator.py` | `check` (the whole beat) and `apply_clip` (one clip); the only code that mutates the world |
| Context | `src/story/context.py` | Every prompt section, so the prompt budget is decided in one place |
| Compiler | `src/story/compiler.py` | One clip → LTX-2.5 prompt text; the only code that knows the video model's dialect |
| Renderer | `src/i2v/ltxv.py`, `server/ltx2_server.py` | The HTTP client and the resident LTX-2.5 server |
| App | `src/app.py` | The session: the film page, Begin or Continue, Stop, and a stop when no page is open |
| Player | `src/web/` | The film page and its server: the picture, Begin or Continue, Stop, and a box to type into |

## A beat, then its clips

**Plan** (one Now call). A finished Then run lands first; one still running is left alone, so the fast clock never pays for the slow one. The viewer's input, if any, is recorded as an open input. Before the story's first clip Then runs once, before anything is on air, to write the opening notebook from Frame Zero's first frame and its character; if that run fails the story begins without a notebook and Then runs again on its usual clock. Now then gets a cached system prompt (its craft, the content policy and the output contract, the same in every story) and a user prompt with the notebook, the world as it is now (every character in the scene with its look), the viewer's inputs, the story's past (below), the recent clips verbatim, and where the picture is (on the first beat, Frame Zero's first frame). It returns a `BeatPlan`. A hard violation anywhere in the beat (an unknown entity, a new character without a look, a line spoken by a prop) earns exactly one re-ask with the violations spelled out. Then runs again every `then_every` clips (10). At most one run is in flight, and runs are at least `then_min_gap` clips apart.

**Emit** (one clip at a time, no LLM). `apply_clip` applies that clip's world changes, so a beat cut short leaves the world exactly as far along as the picture. The compiler turns the clip into one flowing prompt: sound, framing and camera, who is on screen, who enters, the action, the dialogue as `Name says in a … voice, "line"`, and the image the clip ends on. The first clip carries every character's full look and opens on Frame Zero's first frame; later clips carry short looks, and anything entering the picture is described on the clip it enters. There is no style paragraph: how anything is drawn is in its look, so the picture's style changes when looks do. A location with a look is named the same way: in full on the first clip and on the clip the picture arrives there, by its short look while it stays. The clip's record is appended; when the clip goes on air, the story is saved as of it (see below).

**Cut short.** When the viewer types while a beat still has clips to emit, they are dropped, a Now call in flight is cancelled and restarted with the input, and the next plan is told how the beat ended and on which image.

## The world model

Entities (characters, props, locations) carry what code reads in structured fields (kind, present, gone, location) and what only the writers read in prose: each entity has one `state` sentence (a character's pose, place and doing; a prop's place, condition and who has it), replaced whole by the op that changes it. The camera is cinematically free (`camera_location`): when it moves to another location, what is in the picture (or new, or moved by an op) comes along, and everything else stays behind, listed for the writers where and when it was last seen. It goes where something in the picture goes: a character that leaves the picture for another place, or is already out of it, goes there alone. A cut that no op moves lands where its shot evidently is, when nothing in it is from here: the known place it names, or the one place everything it shows was last (going back to a character left somewhere). Characters, props and locations carry a `look` and a `look_short` (restated on every clip they are in), which an op replaces whole when the appearance changes for good (a helmet put on or taken off, a forest under snow). Characters also carry who they are: a `personality` (who it is at heart, from Frame Zero or Now's introduce, rewritten only by Then) and `lately` (how it has been behaving, as Then last wrote it), which stay on the character when the notebook drops it; and a `voice`, which an op may give later. Then rewrites a personality only with a `because` (what in the story changed it), which the roles see beside it. Characters not in the scene are one line each, except those the story is pointing at: left where the picture is, or named (or their place named) by the viewer, the notebook, a character in the scene or the recent clips. Those are shown in full, and the part of the past they were last in comes with them (below), so a return is written knowing who comes back. Then maintains a **notebook** that Now reads at every beat: a portrait of each character who matters now (its personality, which Then leaves empty to keep, and how it has been lately), what is alive, seeds (up to 8) and loose intentions. The world keeps only the inputs the story has not finished with: one pending while its beat is planned, one Now deferred (owed) until a later beat delivers it by its id. The world keeps the recent clips, one record each; only the last `recent_clips` reach Now verbatim, the rest as the story's past, and the story folder's clip log keeps every one. Every list in a prompt is capped (characters and props not in the scene: the 12 seen most recently, the rest counted), so no prompt grows with the story.

**Frame Zero** (`frame_zero.json` in the project root) is the first frame a new story begins from, read only then and copied into it, and nothing more: what it shows (`scene`), the one `character` in it (`name`, `look`, `personality`, `voice`, `state`: what Now gives a character it introduces), the `location` it stands in (`name`, `look`), and the `reference_image` the first clip is rendered from (only a video run needs it; exactly 16:9). An unknown key is an error. All of it becomes ordinary world state: the place can be left, the character's look and state are Now's to change, and its personality is where Then's portrait starts, as for any character. There is no protagonist, no arc, no time axis, and no permanent style. What holds in every story is the **content policy** (`src/prompts/policy.md`), in both roles' system prompts.

## The viewer's memory

The world model is rolled back when a clip never reaches the screen; the viewer's memory (`src/story/viewer.py`) is the person watching, and it never is. It is append-only, `viewer.jsonl`, and every record carries the clip it happened at. Code writes the facts: each input as it arrives, with the clip on air then, what that clip showed and who was in it; what the story did with it (absorbed, deferred, declined, later delivered or let go), recorded when the clip that did it reaches the screen, so a clip rolled back never counts; and when a direction ended (fulfilled, ran its course, superseded). Then writes the interpretation: an understanding of the viewer (a few readings, each citing the inputs it rests on, which code checks and counts; what they lean toward lately; what is unknown) and their marks, the names, decisions, relationships and changes that came from them, each citing its inputs. Nothing about attention is recorded: silence stays unknown.

All of it is kept, little of it is sent. Now reads Then's understanding with each reading's evidence counted, the active direction, and the marks that touch the moment (an entity in the scene, recalled or named) plus the most recent. Then reads its last understanding, the inputs new since its last run (or newly answered) in full, earlier inputs its readings cite or the moment touches, the marks that touch the moment, and how many more are kept. Retrieval is name matching, the same as recalling absent characters; there are no scores or embeddings.

## The story's memory

The story keeps its past at decreasing detail, all of it written by Then and none of it judged by code:

| Level | Written | Covers | Bounded by |
|---|---|---|---|
| Recent clips | by code, each clip | the last `recent_clips` (6), word for word | 6 |
| Summary | by Then, each run (about every 10 clips) | the clips since its last run | the last 3 for Now, 6 for Then, plus those of what comes back |
| Since the last checkpoint | by Then, each run | the current stretch, one paragraph | about 150 words (Now reads at most 300) |
| Checkpoint | by Then, when it sees the story turned at a clip | a name, and a summary of the stretch it closes | those of what comes back |
| The story so far | by Then, at each checkpoint | everything before the last checkpoint: the previous one with the new stretch folded in | about 250 words (Now reads at most 500) |

Rewriting the story so far only at checkpoints keeps it from drifting run by run; a checkpoint whose story so far Then did not rewrite is shown after it until one is. Then is told facts to judge by, never a schedule: how many clips since the last checkpoint, how long each summary has grown, and the clips a new one can be named at.

**What comes back brings its past.** Every summary and checkpoint keeps the entities it involves, which code takes from its clips (who and what was on screen, the places, what entered, what the action and lines name). Something is in play when it is on screen, is the place the picture is in, is recalled, or is named by the viewer's input, their direction or owed inputs, the notebook or the recent clips; it is **coming back** when it is in play but in none of the summaries the role already sees. For each, the checkpoint and the summary it was last part of are put in front of the role, at most 2 and 3 for Now, 4 and 4 for Then. A character or place in every stretch is never coming back; a hermit who returns after two hours brings his past with him. Then reaches back on purpose by naming something in the notebook. Retrieval matches names, as recall does: "remember the storm?" brings nothing back unless the storm was an entity, and the story so far is what covers that.

The records live outside the world, in `history.jsonl`, so the world saved with every clip stays small however long the story runs; the world counts how many it has seen (`history_count`). A rolled-back clip takes the records landed since it back with the world and lands them again.

## The story

Each viewer has one story, and it never ends: it begins once from Frame Zero, and every film continues it from the last clip they saw. It is one portable folder (`story/` by default, git-ignored) that a copy backs up or moves to another computer:

```
story.json                format, id, kind (video; text only for a test story in logs/test-story)
frame_zero/               the Frame Zero it began from, copied once
viewer.jsonl              the viewer's memory
clips.jsonl               every clip the viewer saw, one per line
history.jsonl             Then's summaries and checkpoints, one per line
state/<clip>/             the saved state, the newest only
  world.json              the world as of that clip
  last_frames.mp4         the frames the next clip continues from
```

**Saved as of the last clip the viewer saw.** When a clip goes on air the history records its world has seen are appended, its state is written under `state/<clip>.tmp/`, synced and renamed into place (the commit), the state before it is let go and the clip is appended to the clip log; then the viewer-memory records of that clip are written. The world committed is the one serialised right after the clip was emitted, so it holds nothing the producer prepared later; whatever was emitted but not yet seen is never saved. Every write is durable: append-only lines are synced and each starts on a line of its own, whole files are renamed into place.

**What continues the picture.** LTX-2.5 conditions each clip on exactly one thing, the previous clip's last 9 frames (8k+1 frames, two latent frames; no audio or other state carries over), so that is all the saved state keeps: the last 9 frames of the aired clip, cut losslessly into a 112-275 KB file that decodes to exactly the frames a server restart would restore from. The clip itself is deleted once the next one has replaced it on screen. A clip whose frames cannot be cut keeps the previous ones.

**Resuming.** Opening takes the lock (one process per story; a lock left by a dead process or another machine is replaced), removes unfinished commits, loads the newest complete state (setting aside one that does not load), makes the clip log and the history agree with it and loads the viewer's memory, skipping a line a crash cut off. Each film reads the story from disk again, so nothing an earlier film prepared beyond the screen carries over. Now is told where the picture ends, and the inputs the viewer typed after the story last answered them become owed inputs, recorded as deferred with the first clip that airs (a film stopped before that leaves them unanswered for the next). In memory the world keeps only the recent clips the prompts read; the clip log keeps them all. A crash anywhere costs at most the clips prepared but not yet seen, and a Then run in flight, which runs again.

**On a rented GPU.** The film runs on the box; `server/vast_run.sh` copies the story there first, brings it home every minute and when the film stops, and before `down` destroys the box. A copy brought home replaces the viewer's only if it is the same story and not behind (`main.py adopt`); the replaced one is kept as `story.previous`. The pull sends only what changed, using the viewer's copy as the basis (rsync `--copy-dest`). An adoption cut short is undone on the next start rather than beginning a new story.

A story map, branches and checkpoints the viewer can go back to are being built on the `story-map` branch.

## The film page

`python main.py play` opens the story and serves the film page (`/`); nothing renders and no model is called until the viewer presses **Begin** (the first time) or **Continue**. Before that the page shows the picture the story stopped on: the last of the saved frames, or Frame Zero's image before the first clip (a text story shows the last clip's words). The click also lets the browser play sound. While the film plays, **Stop** ends it, saved as of the clip on air, and the page offers Continue again. A film nobody is watching stops by itself: when no page has been open for 60 s, it stops and is saved the same way, so a closed tab no longer keeps the film running and calling Now. Nothing of that reaches the story or the viewer's memory. A film that cannot start (no OpenAI key, the renderer not reachable or not warm), or stops because it cannot go on (below), says why on the page; the page itself needs neither. Ctrl+C ends the session the same way. While the player listens on `127.0.0.1` it answers only requests addressed to this computer, so a page elsewhere cannot reach it by pointing a name of its own at 127.0.0.1 (DNS rebinding); every POST is JSON only, which a browser cannot send cross-site without a preflight the server never grants.

## Who decides what

- **The viewer outranks the notebook.** An input is a suggestion to the storyteller, not a command: it changes what happens from the beat it lands in, in the story's and the characters' own way (a twist, a character who resists, a consequence later). Now reports each input as `absorbed`, `deferred` or `declined` (only what the content policy rules out, answered in a form it allows). An input the story takes up becomes its direction, shown to Now with its age, what was on screen when it was given and how Now took it, until Now says it is fulfilled or has run its course, or a newer input takes its place; meanwhile the notebook's intentions give way.
- **Characters want things; the story progresses.** Each character acts in its own nature, coloured by what it wants, and both roles are asked to keep the story moving.
- **Personality holds; behavior moves.** Now acts each character from its personality and the moment; a single episode is behavior, and Then rewrites a personality only when the story has shown it change.
- **Nobody is the protagonist.** The first character is a character like any other. The story follows whoever it is most alive around; Then notices when attention shifts, Now moves the camera, and a character left behind stays in the world where it was.
- **Pacing is the roles' judgment.** No code measures stagnation. Now ends a moment that keeps coming round, and Then judges the story's rhythm on every run.

## The live loop

A producer and a consumer on a small queue. The consumer hands rendered clips to the player, which holds each for its length: **the server is the clock**, so the film runs at story pace whether or not anyone watches, and a browser that joins late seeks into what is on air.

The producer renders **just in time**. It builds a lead first (`startup_clips` + 1 clips), then fires each render its estimated render time plus `buffer_seconds` (4.7 s) before the clip is due on air, and asks Now for the next beat as soon as the current one has no clip left. Until the fire it listens: a viewer's input cuts the beat short and Now writes the next beat from it at once, so an input typed during clip N reaches the screen at clip N+2. A beat that answers an input has at most `answer_max_clips` (2) clips, since that call is on the critical path and its latency tracks its length. Now's latency tail is the main remaining stall risk.

**Failures.** A failed render is retried once if the retry can still land in time; otherwise the world and the beat are restored to before the clip and the clip is replanned from the clip before, its viewer input answered again. An exception while emitting takes the same route. Three failed clips in a row stop the film. Now has 20 s per call (`now_timeout_s`); a call that fails (it timed out, was refused, came back cut off at its 4,096-token cap or not as a beat) is made once more, and when that fails too the film stops: the clips already rendered play out, and the page says why. Code never writes a clip of its own, and the viewer's input stays unanswered, owed at the next Continue. Then has 90 s and two retries, and a run that fails changes nothing. Three failed saves in a row also stop the film, at once, rather than let it play on unsaved. Stopping a film cancels a Now call in flight, so nothing runs or bills after it.

**Records.** Every film writes technical records the story never needs, in `logs/films/<id>/`: the transcript (`transcript.md`), the models' raw outputs (`plans.jsonl`, `then.jsonl`) and a timing log (`timing.jsonl`: fires, renders, queue margins, stalls, viewer inputs, Now calls, commits, and what stopped the film). Rendered clips wait in its `clips/` until they have played, and are then deleted: the film is never kept or replayed.

## Picture and sound

The LTX-2.5 server (`server/ltx2_server.py`) holds the distilled model resident on an 80 GB GPU with fp8 matmuls and compiled transformer blocks, at 512×288. It owns a **sliding window**: every clip is conditioned on the last 9 frames of the one before, and only a story's first clip is seeded from Frame Zero's reference image (which must be at the render aspect, 16:9, to the pixel). Nothing re-seeds mid-film, because a re-seed resets costume, props and position to the reference. A film's first request starts a session on the server from either the reference image (a new story) or the frames saved with the last clip (a resume), and nothing an earlier session held carries over; after a server restart the client starts it again from its copy of the last clip's tail. `ltxv_continuity_strength` trades continuity against how strongly a new prompt lands.

**One clip shape.** `clip_seconds: 6` becomes the run's one shape: 153 frames rendered, of which the first 9 repeat the previous clip's tail and 144 (6.0 s) are new. Now is told its length, the engine and story clock count it, the render request carries it and the player holds it. Under fp8 every frame count compiles for minutes, so the server warms the shape at startup and the client refuses a server that has not.

**Seams.** The page keeps three video elements: it cuts the picture to the next clip where that clip's repeated head begins, so every frame is seen once, and crossfades the sound over those 0.375 s with equal power (a clip with speech comes in at once). The server holds every clip under -18 LUFS with its audio padded to the video.

**Sound.** LTX-2.5 renders all of it from the compiled prompt: ambient sound and the characters' lines, in each character's `voice`. There is no narrator and no text-to-speech. Two speakers in one clip render in two distinct voices (checked once on the GPU, by ear).

**Cuts, sparingly.** Now may write "The view cuts to …", which LTX-2.5 follows: never on the first clip, at most one per clip. Cuts land late in the clip and can repaint faces in close-ups, so Now is told to cut only to another place or for a reveal; a walk keeps drawing the old place, so a new place is reached by cutting to it.

## Not built yet

Per-character reference images; retrieval by meaning rather than by name; relationship graphs; multi-viewer input. One story with several locations has played on the GPU: a cut lands the new place, a walk does not.
