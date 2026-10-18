# Decisions

Why the prototype is built the way it is: each entry is a decision, what it was weighed against, and the evidence. The runs behind them were made between September and October 2026 on one story (the LEGO minifigure in a white void), with OpenAI models and LTX-2.5 on rented H100s. They are grounding for the design, not benchmarks; the current performance numbers are in the [README](../README.md#performance).

## Orchestration

**Two LLM roles instead of four.** The first engine split every segment across four LLM roles. It was replaced by one Director call per beat and a Showrunner on a slow clock, over a typed world model that only code mutates. In an A/B over 30 viewer turns the new engine took 178 s and 39 calls against 265 s and 105 calls, and its prop ledger stayed correct where the old one's was wrong on 22 of 30 turns. A 180-segment run afterwards had no failures, tracked 40 props, paid off 42 of 42 story threads and kept the prompt context bounded at about 2,400 words.

**The Showrunner never blocks the film.** The film used to wait for the Showrunner at the next segment. In a live session that and a slow Director call held the picture for 45 s over 27 clip transitions; the Showrunner join alone stalled 2-8 s on a fifth of the segments. Now its update lands at the first plan that finds it finished, the Director runs on a priority tier and writes less, and dry runs held the picture for 0.1 s over 23 transitions.

**Beats, rendered just in time.** The Director writes one beat (one action or state, 1-4 clips) per call and the clips render with no LLM call between them. Each render fires its estimate plus a buffer (4.7 s) before its clip is due, and a viewer's line cuts the beat short and restarts the Director call. A deeper queue alone made playback smooth but pushed a line's effect from two or three clips out to four; rendering just in time keeps both, and a line typed during clip N lands at N+2. Starting the next beat's call as soon as the last clip is out helps with a fast Director and is neutral with a slow one. Admitting lines against a deadline to shrink the buffer traded stalls for latency one for one in simulation and was dropped.

**The viewer outranks the scene card.** Over 14 runs of 40 segments, the Director drifted back to the Showrunner's scene card after 4 of 9 viewer lines. Now a direction stays active for 90 s of film or until the next line, only a card planned after the line clears it, and the card is paused for the Director meanwhile. Removing the Showrunner's planning altogether stopped the snap-backs, but once the viewer went quiet the film barely moved (0.38 structural changes per minute against 1.15, and 151 s without one), so the planning stayed.

**Suggestions, not commands; characters who want things; a film that moves on its own.** In the last four headless films the Director absorbed 3 of 3 viewer lines as written ("exactly as requested"), the Spaceman never went after his flag in either film he was in, no newcomer appeared, and a 119-clip film with no viewer had one prop and six scene cards about it. Its rules asked every new prop for a reason and every possible request to be carried out. Now a line is a suggestion the Director must take up in the story's own way (a twist, a character who resists or does it its own way, a consequence that pays off later), characters act from what they want, and both roles are asked to keep the story progressing instead of needing a reason for each new thing. Not yet measured in a film.

**No code measures pacing.** The engine used to score stagnation (similarity of recent clips, a ladder of nudges, a forced scene end). In one film it reported no pacing pressure on every beat of a ball-passing loop, because bookkeeping changes reset its clock. Both roles now see how long the scene has run and judge for themselves whether the film is going in circles; the Showrunner checks on every compaction.

**One line of prose per entity.** Characters and props carried structured fields (holder, posture, activity, position, status). They drifted apart, invited misreadings (a model wrote `holder: "trumpet"`), and a holder flip counted as a story change on 49 of 119 clips. Each entity now has one `state` sentence, replaced whole when it changes. A 60-segment check had no warnings or retries.

**gpt-5.6-luna for both roles, no reasoning.** 24 Director configurations were compared on 71 beats captured from real films, and Showrunners on 22 cases, judged blind in both orders by two judge families, plus 88 multi-step films and three live films. luna was the only Director to beat gpt-5.4-mini under both judges (win share 0.62 and 0.55) and made the best films; as Showrunner it won 22 of 22 against gpt-4.1. It costs $0.50 per film-hour against $3.88 before. No self-hostable open model directed as well (the best won 35-41 % head to head), and sharing the render GPU with an LLM raised the render p95 by 36 %.

## Picture and sound

**The drift was the sampling recipe, not the prompts.** Early films drifted within each clip (faces, torsos, a greying ground), and a week of prompt work did not fix it. The distilled transformer was being sampled with the full model's recipe (15 steps, CFG 3, STG). On its own recipe (8 sigmas, unguided) the figure holds through the action. The full model on its recipe gave the same identity at 3.5× the time and added an extra character. Remaining "drift" was facial expressions the Director wrote; the LEGO story now forbids them.

**No identity anchor.** Conditioning every clip on the reference image as an extra keyframe suppressed the clip's closing action when placed at the end and inserted an idle beat when placed in the middle. On the correct recipe it is not needed.

**No re-seeding mid-film.** A cut re-seeded from the reference image resets everything the story changed: a cap picked up mid-film vanished, and a re-entry put a second figure on screen. Only a film's first clip is seeded from the reference; every other clip continues from the last 9 frames of the one before. After a server restart the client sends that tail, and the scene survives (a frame difference at the seam of 1.7, against 66 with a re-seed).

**Cuts are a story option; the LEGO story is one take.** LTX-2.5 follows a written cut ("The view cuts to …"): 6 of 6 requested cuts happened and none of 12 unrequested. But the cut lands 2-4 s into the clip, a new angle renders as the figure turning, close-ups repaint the face, and some cuts dissolve. A story may allow cuts; LEGO does not.

**No frame observer.** A vision model checked every rendered clip and could force a cut on drift. After the recipe fix it measured drift that was gone, its forced cut never fired in any run, and it misread framing. Removed.

**One labelled prompt compiler.** Caption-style prose prompts held identity no better and ran 22 % longer. Every shortened version of the visual anchor let hair pieces or printed shirts in by clip 12 on 2 of 3 seeds; the full anchor did on none. The compiler sends the full style and looks on the opening clip and a short anchor afterwards.

**512×288, one clip length.** One frame size, exactly 16:9, for the render and the reference image: identity and detail held by eye against 640×352 at 31 % less render time. One clip length (6 s), because under fp8 every new shape compiles for 90-170 s; the server warms the client's shape at startup and the client refuses to start without it.

**The video model renders all the sound.** LTX-2.5 renders ambient sound and the characters' lines from the prompt; an external narrator and TTS were removed, so every spoken word is a character's. Each clip renders 9 extra frames that repeat the previous clip's tail; the page cuts on them and crossfades the sound, with every clip held under -18 LUFS (no duplicate frames, no luminance dip). The model's own silences between clips still make audible steps; accepted. Two speakers in one clip are not yet verified.

**The saved film is joined the way the page plays it.** Concatenating the clip files kept every clip's 9-frame head, which repeats the clip before: a 20-clip film came out as 3,060 frames, 171 more than the viewer saw, with a stutter at every seam. The film is now joined with each head shown once and the sound crossfaded over it; a 24-clip live film saved exactly the 3,465 frames that played.

## Real time

The transformer's forward passes are compute-bound, so the levers were the GPU, low-precision matmuls, compilation and fewer tokens:

- fp8 matmuls (torchao, dynamic) and regional `torch.compile`: 3.0× to 1.9× real time at 960×544 on an H100.
- Fewer tokens: 640×352, then 512×288 (31 % faster again).
- Frames converted on the GPU and piped to ffmpeg as YUV with 8 x264 threads: render p95 0.89 to 0.67 of the clip's length.
- The client on the GPU box: an MP4 through an ssh tunnel cost 1.5-4.6 s per clip.

What did not help: the full model (3.5× slower, no better), FlashAttention-3 (refuses LTX-2's attention masks), NVENC (unavailable in Vast containers), offloading (28-57 s per clip). A 300-clip film showed no render creep; its three stalls all came from Director calls of 6-14 s, which remain the main risk to a seamless film.

## Known open problems

- **Long films drift to a floor.** As props accumulate, the white void turns into a LEGO baseplate.
- **Continuity against steering.** The sliding window trades one for the other (`ltxv_continuity_strength`); a better mechanism is needed, not a better setting.
- **Director latency tail.** A slow call at a beat's end or on a line can still hold the picture.
