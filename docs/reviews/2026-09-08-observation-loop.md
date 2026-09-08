# Frame observation loop, first live run (2026-09-08)

Setup as in the live run: rented A100, base LTX-2.5 at 15 steps, `observe_frames: true`, `vision_model: gpt-4.1-mini`, 4-segment scenario (`wake / silent / silent / take a bow`), the live config with the sweep's negative prompt. This run started before the blueprint's style text and cast sheet were corrected (see below), so its pictures show the old "photography tent" wording and the "hair piece" sheet.

## Mechanics: all confirmed

| Check | Result |
|---|---|
| Vision call after each render | 4 of 4 succeeded, no errors, no measurable delay to the next segment |
| Landing | every observation landed at the start of the following segment (the `observe_wait_seconds` bound was never hit) |
| Director prompt | the Picture section carried the deviations; the compiler added "is exactly as described, not …" for the character and the setting |
| Cut policy | the Director chose a cut on every segment after being told drift 3; no forced cut was needed |
| Persistence | observations, per-entity `observed_look` and `drift`, and `picture` present in `world.json` |

Contact sheet: `img/2026-09-08-observation-run.jpg` (one row per clip, first/middle/last frame).

## What the loop found

- **The cast sheet contradicted the reference image.** The sheet said "short black classic hair piece"; the reference has a bare head. Every prompt had been asking the model to add hair, and the vision model reported "hair piece missing" against the sheet. The sheet now describes the image. This is the most likely root of the invented-hair artifact that survived every other fix.
- **The old rubric scored everything 3/3**, because printed-face changes and a grey backdrop were treated as "no longer what was described". With `drift_cut_threshold: 2` that meant a re-seed on every segment and no action continuity between clips. The rubric now reserves 3 for a different subject or setting, the default threshold is 3, and a forced cut needs `min_segments_between_cuts` since the last one.
- Cuts from the reference reliably restore the head and body (rows 1–2), but more elaborate actions (rows 3–4: a bow) still pull the picture off within the clip: a halo, a torn-paper backdrop, a printed torso, a dark spotlight. The style wording "like a photography tent" produced literal tents in row 1; the blueprint now says "seamless white cyclorama".

## State after today

- Identity of the head and body: solved for the opening of every cut (reference letterboxed, opening from the reference pose, sheet matching the image).
- Within-clip drift: much reduced for simple actions (the three standing seeds in the sweep are clean), still present for elaborate actions; the loop now measures it and cuts when it becomes a different picture.
- Untested: the full combination now committed (new style text, corrected sheet, negative prompt, reference-pose opening, rubric and spacing) in one live run. That is the next GPU session, followed by the interactive session with a web player.
