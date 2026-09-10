# The full combination, live, through the web player (2026-09-09)

First live run of everything committed since the 2026-09-08 sessions, together: the reworded style text (seamless white cyclorama), the cast sheet that matches the reference image, the sweep's negative prompt, cuts opening from the reference pose, the tuned drift rubric with spaced forced cuts, and the new web player as the viewer surface. Ten segments, steered from the browser input while the film played.

Setup: rented H100 PCIe 80 GB (Vast.ai, $3.12/h, ~50 min including install), base LTX-2.5 at 15 steps, 768×512, CPU offload, `configs/director.live.yaml` with `--player web --segments 10`, GPT-4.1 as Director and Showrunner, gpt-4.1-mini as the vision model. Transcript and world under `logs/live_full/`. Contact sheet: `img/2026-09-09-full-combination.jpg` (one row per clip: first, middle, last frame).

## Mechanics: all confirmed

| Check | Result |
|---|---|
| Web player with real video | 10 clips streamed and played back to back in the browser; the last frame held between renders; no errors in the run log |
| Steering mid-stream | 4 lines posted while the film played (ball, close-up cut, red cap, bow); all 4 recorded, all 4 `absorbed`, at segments 3, 5, 7, 9 |
| Observation loop | 10 of 10 vision calls succeeded; none hit the `observe_wait_seconds` bound |
| Cut policy | the Director opened every segment from 3 on with a `cut` after the Picture section reported drift; no forced cut was needed (0) |
| Showrunner | 3 runs (bootstrap, `scene_done` at 3, `scene_done` at 8); both scenes ended by the Director's flag, not the cap |
| Render latency | 127–155 s per 7–9 s clip on the H100 PCIe with offload (A100: ~200 s); about 17× real time |
| Session | `full_session.mp4` saved; world, events and observations persisted |

## What the pictures show

Every one of the ten clips opens on the plain minifigure in the reference pose on a white ground (left column of the contact sheet). That is the combination working: the letterboxed reference, the corrected sheet, the reference-pose opening and the cut-on-drift policy together make the start of every cut correct. Yesterday's opening artifacts (invented head, hair, halo, tent) are gone from every opening.

Every one of the ten clips has drifted by its middle frame (middle and right columns). The shape is the same each time: as soon as the action starts, the face gains a print (brows, a grin, a scowl), the torso gains a print (an open jacket, a pattern), the ground greys and textures, and a glow or coloured light appears. The drift is not inherited across cuts, since each clip starts clean; it is regenerated inside each clip, within roughly three seconds. It also gets worse as the prompt accumulates content: clips 8–10 (brick plus cap) end on what is effectively a different character, with a helmet, sunglasses, a green face and a patterned torso, and clip 8 adds confetti. Clip 5, the requested close-up, turned the camera move into a blue spotlight on the floor and a halo around the crouching figure although "glowing", "spotlight" and "coloured light" are all in the negative prompt.

Steering landed on screen when the request was a prop or a costume: the soccer ball (clip 3) and the red cap (clip 7) are visible, and the brick the Director introduced on its own (clip 6) renders well and stays in his hands. Camera and blocking requests did not: the close-up became a glow, the "walk off-screen and come back" never left the centre, and the bow (clip 9) is not readable as a bow.

## What this settles

- **Identity at cuts is solved at the prompt level.** Nothing more is needed there; the opening frame of every cut is right.
- **Within-clip drift is not addressable at the prompt level.** The style text, the sheet, the negative prompt and the positive "is exactly as described, not …" corrections are all in these prompts, and the model still redraws the figure the moment it moves. The lever is now the substrate, not the wording: reference conditioning that persists through the clip (per-shot keyframes, an identity adapter, or the distilled model once it works in diffusers), shorter clips (the drift begins around the midpoint of a 7 s clip), and simpler actions per segment.
- **The film is currently a chain of re-establishing shots.** Because every observation reports drift 2–3, the Director cuts every segment, and `continue` is unused after segment 2. That is the right call while clips drift (a continuation would start from the drifted tail), but it means action continuity across segments does not exist yet. This is the continuity-versus-identity trade named in the README, now measured.
- **Under pipelining, observations land two segments late.** Segment N+1 is written while clip N renders, so clip N's observation reaches the Director at N+2. With a cut every segment it changes nothing today; it will matter once continuity is worth protecting. The alternative is to stop overlapping the Director call with the render, at the cost of one Director latency per segment.
- **The rubric misreads "plain yellow head".** Clip 1, the cleanest of the run, scored drift 2 for "a printed face instead of a plain yellow head": the sheet said both "plain yellow head" and "the classic two-dot eyes and curved smile", and the model read "plain" as unprinted. The sheet now says the head is printed only with the eyes and smile.
- **Camera and blocking requests need a different treatment than props.** A prop request compiles to "a soccer ball rolls in" and the model does it; a camera request compiles to a framing change inside a continuous clip and the model turns it into lighting. Camera requests should become the next segment's `framing` on a `cut` rather than action inside the clip.

## State after today

- Working end to end with a real viewer surface: type in a browser, the line is recorded, the Director absorbs or defers it and says why, the next clip shows it (for props and costume), and the film keeps going without input.
- Open, in order: within-clip identity drift (substrate-level), continuity across segments (blocked on the former), latency (17× real time), camera requests as cuts, the second blueprint.
