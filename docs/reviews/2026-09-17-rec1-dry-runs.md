# Recommendation 1 measured: non-blocking Showrunner, smaller Director output (2026-09-17)

Follow-up to `2026-09-17-continuity-and-buffering.md`. The change: a segment never waits for the Showrunner (its update lands at the first later segment that finds it finished, at most one run in flight, `showrunner_min_gap` segments between runs), and the Director's `Segment` lost `narration` and `outcome` (the action beats are the only prose, statuses are end states, `next_direction` is a few words). Then the per-clip call was measured three ways and the live loop was dry-run four times.

## The call itself

Six calls per setting on the saved world of a dry run at segment 24 (four props in play, the busiest state the runs reached), same prompts, `scripts` under `logs/_scratch/investigation-2026-09-17/director_bench.py`.

| Director | latency mean / max | output tokens | tokens/s | beats per segment |
|---|---|---|---|---|
| before the change (user's session, 30 calls) | 6.63 s / 10.3 s | 504 | 41–108 | 4.6 |
| gpt-4.1, default tier, first prompt cut | 6.31 / 7.63 | 374 | 60 | 4.8 |
| gpt-4.1, default tier, beats capped at three | 5.75 / 8.89 | 325 | 60 | 3.7 |
| **gpt-4.1, priority tier** | **2.14 / 2.51** | 333 | 156 | 3.8 |
| gpt-4.1-mini, default tier | 3.50 / 4.37 | 259 | 75 | 3.0 |

Two things follow. The output shrank by a third, not by half: the JSON keys of a strict schema are fixed cost, and the Director keeps writing long beats (17 words on average, up to 33, against an instruction of under fifteen). And on the default tier gpt-4.1's rate of about 60 tokens/s puts even 325 tokens at the clip length. OpenAI's priority processing is the lever that works: the same model, the same output, 2.1 s. gpt-4.1-mini is faster than the default tier but slower than priority, and in its dry run it recorded fewer world changes (holder changes missed while the figure handled a ball) and slipped on the anatomy twice. The live config now sets `director_service_tier: "priority"`; the headless config stays on the default tier.

## The loop, dry-run

`configs/director.live.yaml` semantics (6 s clips, `showrunner_min_gap: 4`) with the replay backend imitating the renderer at a constant 4.75 s per clip, the real Director and Showrunner, 24 segments, three scripted lines, the event-stream probe on the player. Holds are measured between consecutive `playing` events minus the clip length.

| run | Director | lead clips | holds over 23 transitions | Showrunner runs | line typed during N landed at |
|---|---|---|---|---|---|
| user's live session (before) | gpt-4.1 | 1 | 45 s, 16 visible, max 9.3 s | 6, each a 2–8 s stall | N+2 / N+3 |
| gpt-4.1 default | gpt-4.1 | 1 | 0.1 s, none visible | 5, no stall, 4 held triggers | N+4 / N+5 |
| gpt-4.1-mini | mini | 1 | 0.1 s, none visible | 3, no stall, 5 held | N+4 |
| gpt-4.1 default | gpt-4.1 | 0 | 0.07 s over 19, none visible | 4, no stall, 6 held | N+3 / N+4 |
| **gpt-4.1 priority (shipped)** | gpt-4.1 | 1 | **0.09 s, none visible** | 5, no stall, 6 held | N+4 |

In the shipped configuration the late start engaged on 22 of 22 segments with 1.4 s of slack on average, which puts the loop's own Director estimate at 2.3 s; the Showrunner landed one segment after each spawn ("still running" on the segment in between) and never held a Director call; the transcripts show 3.2 beats per segment and 4.3-word statuses.

## What the faster producer costs: steering

With the producer slower than playback, the queue was empty and a viewer's line landed two or three clips after it was typed. With the producer faster, the queue fills and stays full, and the pipeline runs one queued clip, one rendering and one being written ahead of the clip on air, plus the lead. A line typed during clip N is read when the next segment starts being written: N+4 with one lead clip, N+3 with none. That is 18–24 s of steering delay, up from 12–18 s, and no Director speed-up reduces it: the depth is the queue, not the call.

Options, none applied yet:

1. `lead_clips: 0` saves a clip (measured N+3/N+4, still gapless in the dry run) at the cost of one fewer clip of safety against a Director or render tail; the dry run's renderer has no variance, a real one does (4.3–4.9 s).
2. Interrupt on input: when a line arrives while a Director call is in flight, cancel and restart it with the line. Saves up to one clip; one wasted call per interruption.
3. Start the Director as late as the next queue slot allows, not as late as the running render allows (the late start's current rule). Saves up to a few seconds within the same clip, so the line lands one clip earlier about half the time.
4. Re-plan on input: discard the segment written but not yet rendered and rewrite it with the line, rolling the world model back to the snapshot before it. Saves another clip; needs per-segment world snapshots and truncation of the event log.

Together 1–3 bring a line to about N+2 at no cost to playback in the measured regime. They are the next piece of work if steering latency matters more than one clip of safety.

## Files

Dry-run configs, event logs, transcripts and the benchmark script are under `logs/_scratch/investigation-2026-09-17/rec1/`. The runs' Langfuse sessions are `director-dry-gpt41_live_20260917_123733`, `director-dry-mini_live_20260917_123733`, `director-dry-lead0_live_20260917_125100` and `director-dry-prio_live_20260917_130346`; Langfuse's API was timing out while this was written, so the per-call numbers above come from the benchmark and from the loop's own late-start events, not from the traces.
