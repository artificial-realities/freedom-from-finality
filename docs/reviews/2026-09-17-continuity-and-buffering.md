# Where the gaps come from and why the film feels chaotic (2026-09-17)

An investigation, not a change. The subject is the user's session of 2026-09-17 14:34 (`logs/live-box/`, 28 clips on an H100 NVL, fp8, 640×352, 6 s clips, one clip of lead), read against the code, the Langfuse traces of every LLM call, the clip files' timestamps, and three runs of my own: a headless run of the same config with scripted steering, a replay run under headless Chromium to measure the player alone, and a 30-clip live run on a fresh box with scripted steering, a probe on the player's event stream, and a browser measuring the seams. After the live run I rendered prompt variants directly on the server. Raw data, scripts and the browser logs are under `logs/_scratch/investigation-2026-09-17/`; the live run is under `logs/live-claude/`; contact sheets are in `img/2026-09-17-*.jpg`.

## 1. What worked in the session

- **The picture holds.** One cut (the opening) and 27 continues; the figure, the white ground and the props stay on model through all of them. The red cap in my run rendered on a `continue` and survived two clips.
- **The renderer is under real time and steady.** The server generated every clip in 3.97–4.13 s plus 0.56–0.61 s of encode (2.9 s of that is the eight transformer forwards); as seen by the client, from the request to the clip on disk, 5.1–6.0 s. That never varies by more than a second.
- **Steering lands.** All three of the user's lines were absorbed (umbrella at 10, breakdance at 19, sleep at 23); in my run all six lines were absorbed two or three clips after they were typed.
- **The player is gapless when it is fed.** With clips ready ahead of time the browser swaps within 10 ms of the previous clip's end (section 2).
- **The world model does its job for props.** In my run a brick the Director clicked onto the figure's foot stayed there for twelve clips, because it was a status in the world model; the umbrella, brick and cone in the user's run were tracked correctly through hand-offs and a removal.

## 2. The remaining buffering, exactly

### The number

The 28 clip files carry the box's nanosecond timestamps (rsync preserved them). The producer finished a clip every 7.95 s on average (median 7.41 s) against 6.04 s of playback per clip. Replaying the player's queue rules against those timestamps (one clip of lead, the server sleeps each clip's measured duration) gives 45 s of holds over 27 transitions: 1.67 s on average, 16 of the 27 transitions visibly held, the longest 9.3 s (clip 20), then 6.0 s (12), 4.1 s (9 and 27), 3.9 s (21). That is the "1–2 seconds between clips".

### Where the time goes, per clip

The Langfuse traces give the start and end of every Director and Showrunner call; the clip file gives the end of the render. For each segment: the Director call, any wait for a Showrunner at the start of the segment, and the render.

| Stage | Measured | On the critical path? |
|---|---|---|
| Director call (GPT-4.1, structured output) | mean 6.63 s, min 3.8, max 10.3; 504 output tokens on average (293–725) at 41–108 tokens/s; input ≈4.2K tokens of which 2.9K served from OpenAI's prompt cache | **Yes, always.** The next clip's render cannot start before this call returns. |
| Showrunner join at the start of a segment | 2.3, 5.0, 5.1, 5.1, 8.0, 6.9 s on turns 5, 8, 9, 12, 20, 30 (plus 2.8 s for the bootstrap on turn 1): 32 s in total | **Yes, on those turns.** The engine awaits the Showrunner task before it calls the Director; the task was spawned only when the previous Director call ended, so most of its 5–8 s falls inside the next segment. |
| Render, server side | 4.0 s generate + 0.6 s encode | No. It runs in the background while the next Director call runs. |
| Render, as the client sees it | 5.1–6.0 s (HTTP, the 1 MB body, the file write, ffprobe) | No, same reason; and it is under 6.04 s. |
| Enqueue, `playing` broadcast, SSE to the browser | 0–2 ms on localhost; through the ssh tunnel about 0.05 s | No. |
| Browser: swap to the prefetched clip | 9–25 ms local, 1–15 ms through the tunnel when the clip was prefetched; 0.21–0.36 s when the clip arrived at the same moment as the `playing` event (it then has to be fetched through the tunnel first) | Only when the clip is late anyway. |
| Crossfade between the two video elements | 250 ms CSS transition | A visual overlap, not a hold. |

The cycle of the loop is `max(Director call + Showrunner wait, previous render)`, and in 27 of 28 segments the left side won. Summing the session: 183 s of Director calls and 28 s of Showrunner waits against 165 s of render, for 28 clips. The GPU was busy about 58% of the time.

Prompt caching explains one oddity in the traces: the input token count alternates between about 1.3K and 4.5K. Both prompts are the same size (10.6K characters of system prompt, 4.5K of user prompt); the small number is the uncached part. A Showrunner landing changes the system prompt (entity notes go into the cast section), so the segment after every Showrunner pays for the whole prompt again.

### The same picture without a GPU, and again with one

- **Headless, same config, text mode** (`director-headless6_live_20260917_110209`): Director calls averaged 7.8 s (24 calls, one of 24.9 s), the Showrunner ran seven times in 24 segments, and the holds were worse than live: 76 s over 23 transitions, 3.3 s on average. With no render at all, the film still cannot keep up with itself.
- **My live run on a fresh H100 NVL** (`director-ltxv_live_20260917_151456`, 30 clips, `logs/live-claude/`): Director calls (from "writing segment N" to the render being fired, on the player's own status stream) averaged 8.0 s, median 7.1 s, max 17.4 s; the render from firing to enqueue was 4.26–4.88 s every time; 46 s of holds over 24 transitions, 14 of them visible. The four longest Director spans (12.6, 17.4, 12.4, 14.3 s at segments 16, 17, 23, 24) are exactly the segments that waited for a Showrunner: at 16 the compaction interval fired, at 17 `scene_done` fired again, so the loop stalled twice in a row.
- **Player alone** (replay backend, mock LLM, instant producer, headless Chromium): 6.041–6.050 s from one clip's `playing` to the next for 6.042 s clips, SSE latency 0–2 ms, swap 9–25 ms, no `waiting` events. Nothing in the player or the transport is worth a millisecond of work until the producer is under real time.

### Could work happen in parallel or ahead of time?

The render and the Director call already overlap fully; the late-start wait that reads viewer lines never engaged in any run, because it only waits when the Director is faster than the render, and it never is. Since Director(N+1) depends on the world state after Director(N), two Director calls cannot run concurrently. So the only ways to close the gap are to make the per-clip LLM path shorter than the clip, to stop it from blocking on the Showrunner, or to have one LLM call cover more than one clip. A deeper buffer (`lead_clips: 2` or `3`) absorbs the variance but not the mean: at 7.95 s per 6.04 s clip any buffer drains.

### Is gapless realistic?

Yes, with the current player and renderer. The renderer has 1.3 s of margin per clip and the player is already seamless; the seam that remains once the holds are gone is the 250 ms crossfade and an audio cut between clips (each clip carries its own ambient track), both player-side polish. What has to change is the producer: the per-clip critical path must average well under 6 s with low variance, which the current one-Director-call-per-clip design does not deliver with GPT-4.1 writing 500 tokens per clip.

## 3. Why the story feels abrupt

The design treats every 6 s clip as a story beat that must change something, and everything in the Director's context pushes the same way:

1. **The schema demands change per clip.** Every call must return `outcome` ("what has changed by the end") and `next_direction` ("where the next segment should go"). The Director complied: in 20 of the 30 segments the `outcome` or `next_direction` asks to escalate, introduce or invent something; "suddenly" appears in 11 of 30 narrations.
2. **`next_direction` is fed straight back** as "Direction set after the last segment" at the bottom of the Recent segments section, so each call inherits an instruction to move on. In the user's run the sleep segment (23) ended with "Lego Man may now dream, be woken by a new prop or sound, or escalate his sleep gag"; 24 escalated the sleep gag (balancing props on his chest), 25 "jolts upright". In my headless run the sleep request landed at 5, the Director added an unrequested brick in the same segment, set `scene_done`, and by 6 he "stirs and sits up": one clip of sleep, driven by its own `next_direction` ("will discover the red brick").
3. **The prompt's discipline rules are per clip:** "Advance the scene toward its exit condition", "Do not repeat a bit from the recent segments. Escalate, vary, or pay off", "When it says a development is required, deliver one", and the pacing ladder counts segments (6 s each) without a world change. Nothing in the prompt says that continuing the current action is the default.
4. **Scenes are seconds long, and every scene end is a structural reset.** Scenes in the user's run lasted 4, 3, 1, 3, 8 and 10 segments; the Director set `scene_done` as soon as an exit condition like "takes his first deliberate action" was met. Each end spawns a Showrunner whose instructions say "Make sure it changes something structural" and "Progression beats polish", and it writes a new card with five candidate bits. Threads followed the same clock: eight opened, seven paid off within one to three segments. A gag has a 12 s lifespan.
5. **The world model has no notion of what the character is doing.** Props have a status, holder and presence; the character has a free-text `status` that the Director writes as a synopsis of the clip ("holding blue brick above head at first, then mid-bow with blue brick in hand") rather than a state. Posture and activity live only in `end_state` and the narration, which the next call treats as a launch pad, not as something to maintain. When I asked for a quiet segment (12 in the headless run, 15 live) it happened, faithfully, for that one segment; the next segment opened with "suddenly straightens his back, as if struck by a new idea". In my live run "wake up slowly" arrived while he was already sitting, so the Director had him lie down first in order to wake up: the state it was continuing from did not say he was awake.
6. **Too many beats per clip.** The Director packs 4.6 action beats into each 6 s clip (min 3, max 6): "reach for head, balance it like a bowling ball, roll it, retrieve it, click it on, sit up, ta-da" is seven actions in six seconds. Rendered, that is either a rushed blur or a selection the model makes on its own, and it is why individual clips feel like separate events even before the next clip changes the subject.

The "repeat" rule does not even buy variety: in my headless run the Director popped the figure's head off at 15 and then produced six head-stacking variants in a row, each written as a new bit.

## 4. The video prompt: what is unnecessary

The compiled prompt averages 284 words (225–369) per 6 s clip. Its composition over the 29 segments:

| Part | Words | Notes |
|---|---|---|
| Style paragraph | 112, every clip | identical text; 39% of the prompt |
| "On screen" block: character look + prop descriptions | 62 on average | the same 35-word character description on every continue; each prop's full description every clip once it exists |
| Ambient sound | 14 | fine |
| Camera sentence | 8 | fine |
| Action beats | 65 (max 92) | 4–6 beats |
| Final frame | 17 | useful as a target |
| Voice-over line | 24 when present | needed for native audio |

61% of every prompt is fixed text. Lightricks' own guidance for LTX-2.5 (Runware's prompting guide for the model) says a focused brief beats an exhaustive one, one dominant action arc per clip, character described once and then the action; the image-to-video guides say to describe the motion and not the static elements already in the frame. Every continue clip here re-describes what the nine conditioning frames already show.

I rendered the same three-clip scene twice on the live server with the same seeds (`img/2026-09-17-probe-verbose.jpg`, `-compact.jpg`): the current compiler's shape (258, 202 and 214 words) against a compact one (30, 18 and 23 words: "He lies still on his back, asleep, hands on his chest, breathing slowly. Nothing else happens. Static camera.").

- Render time is identical (3.98–4.13 s, text encoding 0.10 s either way). Prompt length costs nothing on the renderer; this is about focus, not speed.
- The compact "nothing else happens" clip is actually still for six seconds. The verbose version of the same instruction had the figure sit up and lie back down.
- The ball entered from the left and stopped beside him in both; he stayed asleep in both.
- A clip written at the current density, five beats in six seconds ("walks three steps, picks up a ball, tosses it, catches it on his head, loses balance, falls"), rendered as a translucent ball appearing mid-air, a motion-blurred figure and the fall (`img/2026-09-17-probe-busy.jpg`).
- An eight-clip compact chain (sleep, still, ball in, still, wake and sit, sit still, pick up, walk out; `img/2026-09-17-probe-compact8.jpg`) rendered every action as asked, including the two quiet clips, but with no anchoring text at all the framing crept in over the chain and the face grew eyebrows and a grin from the sixth clip. The user's 28-clip run, which carried the style paragraph, held its framing throughout. So the fixed text is not what makes the action legible, but a short anchor for framing and the face is still worth keeping on long continue chains; the right length is a test, not a guess.

What the video model needs for the next six seconds: who is in frame in one clause (name plus a short identity anchor), the camera in a few words, one or two motion beats, the final state, a sound cue, and the voice-over line when there is one: about 60–80 words. What does not need to be there on a continue: the style paragraph, the prop descriptions after their first appearance, the "On screen" list, the narration's adjectives. Reasons and story belong to the Director's context and the world model; the world model's entity descriptions stay the source for the first appearance of a prop and for cuts.

## 5. Other problems found

- **Cuts are chosen by the Director for re-entries and reset the picture.** In my run the Director cut at 19 and 22 ("re-enters from the left"). A cut re-seeds from the reference image: the brick on the foot and the ball vanished at the cut, and at 19 the reference figure stood centre frame while a second figure walked in from the left for a moment (`img/2026-09-17-live-claude-ball.jpg`). The system prompt invites cuts ("Use `cut` for a new angle or a new moment"); with the current re-seed that is a continuity hazard whenever the look differs from the reference.
- **Showrunner triggers stack.** The compaction interval and `scene_done` fired on consecutive segments (16 and 17 in my run), each a synchronous stall. `last_showrunner` is used only for the stagnation trigger.
- **The Showrunner runs far more often than "a slow clock":** 7 runs in 30 segments in every run I looked at, one per 4 segments.
- **Entity statuses narrate instead of stating**, and the character's `holder` ended up set to itself in the world model (a character update with `holder: lego_man` is accepted by the validator).
- **Every Showrunner landing invalidates the prompt cache** (entity notes are rendered into the system prompt): the next Director call pays for 4–5K uncached input tokens and is a little slower.
- **Steering latency is two to three clips (12–18 s)**, and the late-start mechanism that was built to make it two never engages because the Director is slower than the render. It will start to matter once the Director is fast.
- **In the busiest clips the picture still drifts a little**: the cap disappeared during the juggling clip (27) and a head vanished for a few frames in the brick-on-foot clip (10) in my run; both are clips with four or more beats.
- **Audio at the seam.** Each clip has its own ambient track; once the holds are gone the cut in the ambience will be the most audible seam.

## 6. Recommendations, by expected impact

1. **Take the LLM off the per-clip critical path: never wait for the Showrunner, and cut what the Director writes per clip.** Land the Showrunner's update when it arrives, at whatever segment starts next, and keep the old plan meanwhile; rate-limit its triggers (a minimum of N segments between runs, `scene_done` should not force a synchronous replan). Shrink the Director's output: today it writes the same six seconds four times (narration, action beats, outcome, entity statuses); keep one prose field or the beats, derive the transcript text from it, cap statuses to an end state, drop or shorten `outcome`/`next_direction`. Target 150–250 output tokens, which at GPT-4.1's rate is 2–3 s, or try `gpt-4.1-mini` for the per-clip call once the call is small. Then raise `lead_clips` to 2 or 3 to absorb the tail. Expected: production at roughly 4–5 s per clip, no holds, at no cost to the design. This is the cheapest change and removes the whole visible symptom. *Done 2026-09-17: non-blocking landing with `showrunner_min_gap`, and `Segment` without `narration`/`outcome`; measured in the dry runs recorded in `docs/reviews/2026-09-17-rec1-dry-runs.md`.*
2. **Change the unit of direction from a clip to a beat.** Let the Director write a beat with a target length in clips (say 2–5) and an explicit state (posture, activity, what is held, where), and derive each clip's prompt from the beat by code ("he keeps sleeping; nothing else happens") or by a very small, fast call; call the Director again only when the beat ends, when the viewer types, or when the plan says so. This is what makes states persist by construction, lets scenes breathe, replaces "what happens next" with "what naturally follows", and makes the per-clip critical path trivially short. The separation stays clean: Showrunner for memory and plan, Director for story reasoning at beat granularity, world model for state, compiler for the visual instruction.
3. **Give the character a real state in the world model** (posture, activity, held items, facing, position in frame), mutated only by ops, rendered into the prompt as "Now: lying on his back, asleep, brick on chest", with the instruction that the next clip continues it unless an op changes it. Move the pacing ladder from segments to minutes of story time.
4. **Rewrite the Director's per-clip rules.** Remove "escalate, vary, or pay off" and "advance toward the exit" as per-clip discipline; say that most clips continue the current action, that a new action needs a reason, at most one new thing per clip, one or two beats per six seconds; stop feeding `next_direction` back as an instruction (or make it a beat-level field); make scene exit conditions things that take minutes. Tell the Showrunner that a scene is a few minutes, not a few dozen seconds, and that "changes something structural" applies between acts, not every scene.
5. **Compile a compact visual prompt** (recommendation 4 in the user's numbering): a short identity and framing anchor, the motion, the final state, a sound cue, the voice-over; the style paragraph and prop descriptions only on cuts and first appearances. Validate on a ten-clip continue chain against the current compiler, watching framing and the face; the eight-clip probe says an anchor of some length is needed, and the right length is an experiment.
6. **Stop cutting while the look differs from the reference.** Either forbid `cut` unless the world is at reference state, or re-seed cuts from a keyframe of the current look (the deferred costume problem is now also a duplicate-figure problem). *Done the same day, on the user's decision: cuts and multi-shot segments were removed entirely; the renderer re-seeds only on the first clip of a run and after a server restart.*
7. **Player polish after the holds are gone:** crossfade the audio at the seam, and shorten or keep the 250 ms video crossfade by taste. Nothing else in the player needs work. On the server, overlapping the 0.6 s encode with the next clip's forwards would need an asynchronous submit API and only matters once the render is the bottleneck again.

*Status, end of 2026-09-17: 1 and 6 were implemented and measured in dry runs (see `2026-09-17-rec1-dry-runs.md`); 2, 3, 4, 5 and 7 were implemented later the same day on the user's instruction, together with the steering follow-ups (just-in-time rendering and interruption on input), without a live run. The next session starts with one.*

Not recommended now: a bigger buffer alone (it does not fix a mean above real time), a faster GPU (the GPU is idle 42% of the time), Media Source Extensions in the player (the swap is already within 10 ms), or a streaming autoregressive video model (Rolling Forcing, LongLive, Self-Forcing and Causal Forcing generate multi-minute video in real time on one GPU from a prompt stream and would remove the clip boundary altogether, but that is a substrate change for later, not the MVP).

### A note on generality

The Lego blueprint is one story; the platform will carry many. Every recommendation above is meant for the generic layer (`src/prompts/*.md`, the schemas, the compiler, the engine), phrased in terms of the blueprint's fields and of generic state (posture, activity, held items, position, presence), never in terms of the minifigure. The per-clip beat limit follows from `clip_seconds` and the video model, not from the story, and belongs next to the existing duration rule. Story-specific text (the fixed face, the anatomy, the single character, how props enter) stays in `data/story.json`. The Lego prompts I rendered on the server were test material, not proposed text. One generic gap the cut analysis exposes: the renderer seeds every cut from the single `i2v_seed_image`, which a story with two characters or two locations cannot use; whatever replaces the current cut needs a per-scene or per-shot seed.

## Appendix: how the numbers were obtained

- Langfuse sessions: `director-ltxv_live_20260917_143416` (the user's), `director-headless6_live_20260917_110209` (headless), `director-ltxv_live_20260917_151456` (my live run); every Director and Showrunner generation with start, end, tokens; the turn spans give the Showrunner wait as the gap between the span start and the first Director call.
- Clip timestamps: `stat` on `logs/live-box/video/turn_*_20260917_*.mp4` (rsync 3.2.7 preserves nanosecond mtimes); render completion = file write.
- Queue simulation: the consumer starts when two clips are queued and holds each clip for its measured 6.042 s; `play` starts at `max(previous end, clip ready)`.
- Player timeline in my run: a script on the player's `/events` stream timestamped every `status`, `queued`, `playing` and `input` event; the browser measurement used Playwright's Chromium with listeners on both video elements and a wrapped `EventSource`.
- Server probes: `POST /generate` through the `up` tunnel with the live config's negative prompt, the letterboxed reference, seeds 1234–1236 per position; contact sheets are frames 0, 48, 96 and 143 of each clip.
