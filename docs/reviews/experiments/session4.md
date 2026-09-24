# GPU session 4 — RUN-17's standard size, LTX-15's render check, RUN-16's live film

**Run:** 2026-09-24, 17:16-17:57 UTC · **Code:** `ca109e0` (BATCH_033-036) · **Box:** Vast H100 80GB HBM3 (SXM), Intel Xeon Platinum 8568Y+, offer 48704113, ≈ $2.3 (41 min; the invoice was not yet posted) · **Driver:** `logs/batch-037/session4.sh`; inputs `scripts/experiments/session4_prepare.py`, analysis `scripts/experiments/session4_analyse.py`

By-eye criteria were judged by the implementing agent from contact sheets (no viewers, D-10).

## RUN-17 — 512×288 against 640×352

**Speed** (`scripts/latency_probe.py --warm 30`, 153 frames, the same box, server restarted at the new size; `logs/probe/s4-640/`, `s4-512/`):

| Size | Ratio p50 / p95 / max | Render wall (session A's chain) |
|---|---|---|
| 640×352 | 0.810 / 0.843 / 0.848 | 4.78 s |
| 512×288 | **0.567 / 0.582 / 0.597** | **3.6 s** |

This box was slower than session 2's (0.669 at 640×352): its x264 encode took 0.89 s against 0.49 s. The transformer went from 2.42 s at 640×352 (session 2) to 1.54 s. Tokens are linear: 144 per latent frame against 220. The restart and its compile at the new size took 4 minutes.

**Picture** (session A's segments 1-12 from the reference, `logs/experiments/gpu4/run17/`: `sheet-s0.png`, `sheet-s1.png`, `detail-s0.png` at native pixels):

- **Identity and detail** are the same at 512×288 as at 640×352.
- **Drift is the same in both**: eyebrows by clips 8-12, and the hollow bottoms of the legs when he sits.
- **The background**: the 16:9 reference arm's is the purest white. Both letterboxed-square arms go grey and mottled, and seed 1's 640 arm smears the face print in clip 8.
- **The opening**: the medium-shot 16:9 reference (a 768×432 crop of the owner's master, the figure at 75 % of the height) opens at the prompt's medium shot. X-12's zoom jolt is gone.

**Criterion met**: 512×288 is adopted with that reference (BATCH_037).

## LTX-15 — the render check of the compiled cut

A scripted four-beat film through the production engine and compiler, the Lego story with cuts allowed, two seeds, and the same film without cuts (`logs/experiments/gpu4/ltx15/*/sheet.png`):

| Written | Seed 0 | Seed 1 |
|---|---|---|
| Clip 3: cut to a close-up of the ball at his feet | cut, ≈ 2.3 s in | cut, ≈ 2-4 s in |
| Clip 4: stays in the close-up | held; the hand lifts the ball out | held; a top-down view with a sly grin mid-clip |
| Clip 5: cut back to the medium shot | cut, ≈ 2.2 s in | cut, ≈ 4 s in |
| Clip 6: cut to a side view as he walks | the figure turns to walk, same camera | the same |
| Control (14 clips, no cut written) | no cut | no cut |

- Written cuts in and out of a close-up happen (4 of 4). None happened unwritten (0 in 14 control clips and 0 elsewhere in the cut arms).
- They land 2-4 s into the clip, not at its start. The compiled clip opens on the old shot and gives it nothing to do, so the model holds it before cutting.
- A cut to a new angle on the same subject renders as the figure turning, not the camera moving (2 of 2).
- **Close-ups drift the face** (a sly grin with eyebrows in both seeds), which the Lego blueprint forbids. The control's face stays the printed smile.

**The live films' cuts.** The real Director with the story's guidance wrote 8 cuts in film A and 7 in film B (`logs/live-box/session4{a,b}/cuts-sheet.png`):
- Close-ups on hands and props render.
- Angle changes again render as turns.
- 4 of film B's cuts render as dissolves (double exposures), not cuts.
- 3 clips degrade: a cut to a wide shot dissolves into mush (A, 57), a smeared transition (A, 62), and a close-up that tears the props (B, 18).

**Criterion not met for the Lego story.** Cuts are controllable but cost picture: they land late, and close-ups repaint the face. The Lego story keeps `cuts: false` (proposal in `08_DECISION_UPDATE_PROPOSALS.md`).

## RUN-16 — two live films at the new standard, gpt-4.1 against gpt-5.4-mini

Both films: 64 segments, 512×288, the 16:9 reference fitted strictly, the Lego story with guided cuts, the nine scripted lines of session 2 (`logs/live-box/session4a/`, `session4b/`):

| | Film A: gpt-4.1 | Film B: gpt-5.4-mini, no reasoning |
|---|---|---|
| Render ratio p50 / p95 / max | 0.602 / 0.650 / 0.662 | 0.576 / 0.616 / 0.634 |
| Stalls | 0 | 0 |
| Lowest queue margin | 0.48 s (**FAIL**, ≥ 1 s) | **2.31 s** |
| Director plans: mean / p95 / max | 4.03 / 6.07 / 7.15 s | **3.38 / 4.45 / 6.34 s** |
| Answering a line: mean / max | 4.68 / 6.48 s | **2.96 / 3.67 s** |
| Beats (clips per beat) | 47 (1.4) | **31 (2.1)** |
| Steering p50 / p95 | 11.3 / 13.4 s | 11.3 / 13.4 s |
| RUN-04 gates | 1 failed | **all pass** |

- **Film B's seams** (`scripts/seam_check.py`, 24 clips from turn 20, overlap mode) pass every gate: 0 duplicate frames shown, a 0.06 % dip, swap p95 6.7 ms, no reloads.
- **Its audio** (`scripts/audio_check.py`, all 64 clips): no clip above the ceiling, no true-peak overs, no clicks. Seam step (19.1 / 49.6 dB) and silence holes (23.3 per minute) fail as in session 2, which D-03 accepted.
- **Film B is the new baseline** (`benchmarks/runtime/baseline.json`; session 2's kept as `2026-09-24-session2.json`).

**The environment drifts in every long film.** The white void turns into a LEGO baseplate floor (studs, a grey grid) as props accumulate:
- Film B from about segment 9.
- Film A by segment 25, with a wooden floor for a while.
- Session 2's film at 640×352 without cuts by segments 21-29 (`logs/experiments/gpu4/env-drift.png`).

This predates today's changes. It is the "greying ground" of the busiest clips grown over a film. One film per Director cannot say whether gpt-5.4-mini's denser clips speed it up. It goes to the owner as a finding.
