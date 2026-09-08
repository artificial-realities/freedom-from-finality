# Style, negative-prompt, continuity and opening-pose sweep (2026-09-08)

Harness: `scripts/style_sweep.py` against the LTX-2.5 server on a rented A100 (base model, 15 steps, 768×512, CPU offload). No LLM: fixed prompts, fixed seed, one cut clip from the reference and one continuing clip per variant, 5 s each. Rows of the contact sheets show four frames of clip 1 then three of clip 2.

## Part 1: style text, negative prompt, continuity (seed 1234, 7 variants, 14 clips)

`img/2026-09-08-sweep-style.jpg`

| Variant | Style | Negative prompt | Strength / window | Result |
|---|---|---|---|---|
| base | story.json paragraph | server default | 1.0 / 9 | halo over the head by frame 2, green face, hair by frame 3, grey stage |
| s1 | concise photographic | server default | 1.0 / 9 | same |
| s1n1 | concise photographic | extended (green skin, glowing, spotlight, grey, hair, …) | 1.0 / 9 | halo and hair unchanged; **face stays yellow** |
| s1n1_085 | as s1n1 | extended | 0.85 / 9 | as s1n1 |
| s1n1_070 | as s1n1 | extended | 0.70 / 9 | as s1n1 |
| s1n1_f17 | as s1n1 | extended | 1.0 / 17 | as s1n1; clip 2 background a little cleaner |
| s2n1 | + "stop-motion feel" | extended | 1.0 / 9 | as s1n1 |

Conclusions: the style phrasing does nothing to the halo, the hair or the grey background; continuity strength and window make no visible difference to identity in the second clip; the extended negative prompt reliably removes the green face and is adopted in `configs/director.live.yaml`.

## Part 2: opening pose and seed (4 clips)

`img/2026-09-08-sweep-pose.jpg`

Every clip in part 1 opened with "Lego Man lies on his back" while the reference shows him standing, so the model had to morph the pose in the first frames.

| Variant | Opening | Seed | Result |
|---|---|---|---|
| stand_s1234 | standing, from the reference pose | 1234 | white void held, classic face, a faint tuft late |
| stand_s7 | standing | 7 | clean: white void, classic face, no hair |
| stand_s99 | standing | 99 | clean |
| lie_s7 | lying down (control) | 7 | halo, grey stage, hair: the part-1 artifact on a different seed |

Conclusion: **the opening-pose mismatch is the cause of the halo, the grey stage and the invented hair, not the seed and not the style text.** A clip that re-seeds from the reference must open from the pose the reference shows.

## Changes made

- `Character.reference_pose` in the blueprint; the cast sheet tells the Director what the reference shows and the system prompt says a cut opens from it.
- The compiler adds "The shot opens with <name> <reference pose>, then the action begins." to every shot marked `cut`.
- `ltxv_negative_prompt` in the live config carries the extended negative prompt.
- Style text, continuity strength and window left as they were.

## Open

Within-clip drift after a correct opening is now small (a tuft, an expression change). The remaining question is how many continuous clips identity survives before a cut is needed, which the observation loop measures and enforces (`drift_cut_threshold`).
