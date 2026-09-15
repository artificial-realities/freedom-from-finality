# The recipe was the drift: substrate sweep, profile and live run (2026-09-15)

The 2026-09-09 full-combination run ended with "within-clip drift is not addressable at the prompt level". The same evening, reading the LTX-2.5 model card against the server showed that every run so far had used the distilled transformer with the full-model recipe (`2026-09-09-substrate-findings.md`). This session rented an H100 PCIe 80 GB on Vast.ai and tested that finding: the substrate sweep on the distilled recipe, the same sweep on the full recipe, a profile of where a clip's time goes, and a ten-segment live run through the web player with the same four steering lines as on 2026-09-09.

Setup: `server/vast_run.sh up` (H100 PCIe, $2.54/h, 57 minutes, $2.43 in total), `LTX2_VARIANT=distilled` then `full`, 960×544 (the card's recommended size), 5 s clips in the sweep (121 frames), seed 1234, `scripts/style_sweep.py` substrate set. Clips under `logs/sweep/{distilled,full,distilled_text,distilled_gpu2}/`.

## 1. Distilled recipe: identity holds, with or without the anchor

`img/2026-09-15-substrate-distilled.jpg` — one row per variant, four frames of the cut clip (the kick) then four of the continuing clip (steps and a shrug).

| Variant | Anchor | Clip 1 (cut, kick) | Clip 2 (continue) | Render s |
|---|---|---|---|---|
| kick | off | ball rolls in, kick, ball leaves; face, torso and ground unchanged throughout | two steps right, shrug with both hands; unchanged | 126 (warm-up), 75 |
| kick_a095 | 0.95 at the last latent frame | same kick, starts later; ends in the rest pose | walk, but the shrug is suppressed: the clip must end on the reference | 59, 59 |
| kick_a070 | 0.70 at the last latent frame | as a095 | as a095 | 59, 56 |
| kick_amid | 0.95 at latent frame 8 of 16 | kick with an idle beat in the middle | shrug survives; idle beat mid-clip | 57, 57 |

Every clip in the 2026-09-09 run drifted within about three seconds of the action starting: printed face, printed torso, grey textured ground, glow. None of the eight clips here does, on the action that drifted most reliably, with the live blueprint's style text and negative prompt unchanged. The only thing that changed between 2026-09-09 and today is the sampling recipe: the fixed eight-sigma schedule with guidance off, instead of fifteen linear steps with CFG 3 and STG 1 on weights that were distilled to run unguided. Over-guidance on distilled weights was the drift.

The anchor is therefore not needed for identity, and it costs motion: an end anchor forces the clip back to the reference pose, which removes whatever the action was supposed to end on (the shrug), and a mid anchor inserts a still beat. `ltxv_anchor_strength` stays at 0.0 in `configs/director.live.yaml`. The mechanism is kept for cases where it may earn its place later (a clip that must end on a known frame).

## 2. Full recipe: same identity, one artefact, 3.5× the time

`img/2026-09-15-substrate-full.jpg` — `transformer_full/`, 30 steps, CFG 3.0, STG 1.0, dynamic shifting, variants `kick` and `kick_a095`.

Identity holds as on the distilled recipe. The action is slightly less legible (the walk in clip 2 is closer to stepping in place) and the anchored continuation grew a second, smaller minifigure walking in from the left in the middle of the clip, an "extra character" artefact the distilled recipe did not produce. Render time 201–220 s per 5 s clip against 57–75 s. Nothing on this material argues for the full recipe; the distilled one is the live default.

## 3. Where a clip's time goes

The server now logs per-stage timings (`_install_stage_timers` in `server/ltx2_server.py`: text encoding, condition encoding, transformer compute and transfer, VAE decode, audio). Two findings.

**The forwards were never the cost.** On the full recipe, 90 transformer forwards take 156–169 s (1.7–1.9 s each); on the distilled recipe 8 forwards take 10.9 s (1.36 s each). Yet a distilled clip with the previous `LTX2_OFFLOAD=cpu` placement took 57 s: the transformer's 44 GB was moved to the GPU before the loop (about 4 s) and back to the CPU when the VAE ran (another 4 s, charged to a "VAE decode" of 18 s that is really 0.6 s), plus the same round trip for the 24 GB text encoder.

**Keeping the DiT resident halves the clip.** `LTX2_OFFLOAD=text` keeps the transformer, VAE, audio VAE and vocoder on the GPU (39.8 GB) and hooks only the text side, so the Gemma encoder is on the GPU only while it encodes. The pipeline's end-of-call hook reset, which would move everything back to the CPU, is disabled in this mode.

| Placement | Clip (5 s, 960×544) | Text | Transformer (8 fwd) | VAE decode | Other |
|---|---|---|---|---|---|
| `cpu` (every component offloaded) | 57 s | 5–9 s | 11 s + 4 s in | 18 s (incl. 4 s DiT out) | ~16 s |
| `text` (DiT resident, Gemma offloaded) | 27.8 s | 3.1 s | 10.9 s | 0.6 s | 13.1 s |
| `gpu` (everything resident, 70.2 GB) | 12.4–13.3 s | 0.1–0.8 s | 10.9 s | 0.6 s | 0.5 s |

The 13 s "other" of the `text` placement was the text encoder's move back to the CPU: it happens inside the connectors, which run after `encode_prompt`, so it escaped the text timer. **Everything resident removes it.** `LTX2_OFFLOAD=gpu` drops the prompt enhancer (10 GB, never used by this server) and the duration head and moves the rest to the GPU: 70.2 GB before activations, and the VAE decode still fits. A 5 s clip is then 12.4–13.3 s, of which 10.9 s are the eight forwards. That is the floor for this model at this size; 2.5× real time for a 5 s clip, and real time needs a different substrate (fewer frames per clip, a smaller size, or a next-shot model).

**The encoder was the other half.** Outside the pipeline, diffusers' `encode_video` (PyAV, libx264 at its defaults, one thread, frame by frame) took 17–24 s per 5 s clip on this box, more than the generation it followed. The server now pipes the uint8 frames into ffmpeg (libx264 `veryfast`, all cores) and muxes the audio from a temporary WAV: 1.2 s, same frames, same audio track. From request to file a 5 s clip is now about 14.5 s; an 8 s clip should be about 22 s. The live run below was rendered before these two changes.

`server/vast_run.sh` and `server/remote_setup.sh` now default to `LTX2_OFFLOAD=gpu`; `text` is the fallback for a card under 80 GB.

## 4. Live run: ten segments through the web player

Same protocol as 2026-09-09: `configs/director.live.yaml` with `--player web --segments 10`, GPT-4.1 as Director and Showrunner, gpt-4.1-mini as the vision model, and the same four viewer lines (ball, close-up, red cap, bow), this time posted by `scripts/steer.py` when segments 2, 4, 6 and 8 started playing. Distilled recipe, `LTX2_OFFLOAD=text`, anchor off. Transcript, world and clips under `logs/live_substrate/`. Contact sheet: `img/2026-09-15-live-substrate.jpg` (one row per clip: first, middle, last frame); compare `img/2026-09-09-full-combination.jpg`.

| Check | 2026-09-09 | 2026-09-15 |
|---|---|---|
| Torso, legs, ground inside a clip | drifted in 10 of 10 clips (prints, grey textured floor, glow) | held in 10 of 10 |
| Transitions | cut on every segment from 3 on | cuts at 1, 5, 7 (two shots) and 9; segments 2–4, 6, 8 and 10 continue from the previous tail |
| Render | 127–155 s per 7–9 s clip | 32–39 s per 6–9 s clip (4.3–4.7× real time; `text` placement and the PyAV encoder, before section 3's last two changes) |
| Steering | 4 lines, absorbed at 3, 5, 7, 9 | 3 lines absorbed at 5, 7, 9; the bow, posted at 8, would have landed at 11 |
| Props and costume on screen | ball, cap | ball (clips 5–8, 10), cap (clips 9–10) |
| Camera request | close-up became a spotlight | close-up became a cut to a close-up, of the man with the ball on his head rather than of the ball |
| Observations | 10, all drift 2–3 | 8 landed in time: drift 2, 2, 2, 1, 2, 0, 3, 2 |

The film is now continuous for stretches of three or four segments, which did not exist on 2026-09-09: clip 2 opens on the hand-to-head pose clip 1 ended on, clip 3 on the end of clip 2, and so on until the Director cut at 5 on the first drift report. Action continuity across segments is a fact rather than a hope, and the cut-on-drift policy is back to being a policy instead of a reflex.

What the observer still reports is the face. Every drift 2 or 3 names an eyebrow, a frown, a smirk or an angry expression, and the transcript shows where they come from: the Director wrote "eyebrows" fourteen times, "angry" five, "smirk" five, "determined" twice. The blueprint's constraint that the head "has one permanently printed expression" reads as anatomy, so the Director keeps emoting through the face as it would for any character, and the video model paints what it is told onto the head. That is a prompt fix, not a substrate one, and `data/story.json` now carries a constraint that names the words to avoid. The observer is right to score these; the sheet is what it should match.

Two things remain substrate-level. Clip 10, the busiest prompt (cap, ball, kick, a face close-up), ends on a redrawn figure with a bigger ball and a stylised face: accumulated content still pushes the model at the end of a long clip, though far less than clips 8–10 did on 2026-09-09. And clips 7 and 8 carry a small floating fragment at the left edge, a leftover of the two-shot segment.

Steering lag is now the engine's, not the renderer's. A line posted while segment N plays is read at the start of the next Director call, and under pipelining that call is for segment N+3 (segment N+1 is rendering, N+2 is being written). On 2026-09-09 the 2.5-minute renders hid this. With 35-second clips it is the dominant delay between a viewer's line and its appearance, and it is the same mechanism that lands observations two segments late; the fix is the one already listed under engine items (do not overlap the Director call with the render, or let a line interrupt the segment being written).

Fixed the same day, after this run: the loop now starts the Director call for N+2 as late as the render of N+1 allows (render estimate minus Director estimate, both running averages) and reads the viewer's lines then. A line typed while clip N plays lands at N+2, the earliest the pipeline permits, and no render waits for it; `tests/test_live_timing.py` pins it.

## Mechanics fixed on the way

- `server/vast_run.sh restart` killed its own shell: `pkill -f ltx2_server.py` matched the ssh command line that also carried the start command. Kill and start are now separate ssh calls.
- The Vast ssh proxy (`sshN.vast.ai`) refused the registered key for the whole session while the direct port on the host's public IP accepted it; `up` now prefers the direct port.
- `server/vast_run.sh restart` also hung until the server it started died: the backgrounded process kept the ssh channel open. The remote start now runs under `setsid -f`.
- `scripts/style_sweep.py --out` keeps one output root per server variant; `variant.json` records the server-reported latency of each clip; a single-variant sweep no longer crashes the contact sheet.
- `data/story.json` gains a constraint that the face is a fixed print and names the expression words the Director must not write.
- `scripts/steer.py` is a scripted viewer: it posts lines into the web player when given segments start, so a live run can be repeated without someone at the browser.
