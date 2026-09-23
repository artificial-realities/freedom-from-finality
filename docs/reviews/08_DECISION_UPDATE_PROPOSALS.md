# 08 — Decision update proposals (owner review required)

Experiments of the final decision record (`docs/reviews/05_consolidated_decision_record.md`) that have reported. Each entry gives the result and a recommended disposition; the owner decides whether a gated row changes status. The decision record is not edited by the implementation loop, and no gated production behaviour has been implemented or enabled. Newest first.

## X-02 and X-13 → LTX-12, AUD-02, AUD-03, RUN-12 (and AUD-09's reopening) — 2026-09-23

Report: `docs/reviews/experiments/X-02-X-13.md`. Data: `logs/experiments/x02/`, `logs/experiments/x13/`.

- **Result.** The candidate player (a pool of three elements, a cut at the overlap by the outgoing clip's media time, an equal-power audio crossfade) passes every picture gate in headless Chromium (0 duplicate frames, 0.01 % dip, swap p95 9.8 ms, 0 reloads; today: 9, 25 %, 2.7 ms, 6). The played mix fails the seam-step criteria in every arm (candidate 26.5 dB median, today 21.5 dB; ≤ 12 dB required) because 37 of 63 clips end in digital silence and the next opens on sound; holes remain at about 27 per minute. The −18 and −20 LUFS ceilings both meet X-13's offline criteria (narrated-to-next 13.8 and 11.8 LU, no true-peak overs, no quiet clip touched); the listening preference has not run.
- **Proposed dispositions.**
  - **LTX-12 / AUD-02 / AUD-03:** stay gated until X-03 (GPU). The owner should decide whether the seam-step criterion is re-stated to measure the join itself (for example, only where both sides carry sound): as written, no player-side design can meet it with LTX's clip-to-clip silence.
  - **AUD-03's value:** −18 LUFS, pending the listening half of X-13.
  - **RUN-12:** close (the pooled two-element design meets every swap gate; MSE is not needed).
  - **AUD-09:** its reopening condition is met (holes remain after the seam work); reopen and run X-14 if room tone is wanted.

## X-01 → AUD-05 — prepared, blocked on listeners

`docs/reviews/experiments/X-01.md`: 25 blind pairs are in `logs/experiments/x01/listen/`; three or more listeners are needed.

## X-07 and X-08 → ARCH-08, ARCH-09 (and ARCH-01's validation) — 2026-09-23

Report: `docs/reviews/experiments/X-07-X-08.md`. Data: `logs/experiments/story-authority/` (14 real-Director runs of 40 segments, `summary.json`, `judgments.json`).

- **Result.** Momentum on held a viewer direction 10 s longer on average (55.7 s against 45.6 s; 4 of 9 lines pulled back to the card against 5 of 9), with large run-to-run variation. A Showrunner card landing cleared the momentum six times, five of them 18-48 story seconds after the line, and the returns to the card follow those clearings. Without a card (memory-only) the silent film moved least (0.38 structural changes per minute against 1.15) and had the longest static stretch (151 s). Hiding the candidate beats while a direction is active (E5) held directions longest (104 s against 82 s) and cut card-following during an active direction from 7 beats in five production runs to 1 in three; alone it lowers movement after the last line (0.57 per minute), because momentum never expires.
- **Proposed dispositions.**
  - **ARCH-08 → APPROVED as the "momentum stays" branch:** a landing clears momentum only when its run was spawned after `viewer_momentum_segment`; momentum ages out after a configurable number of story seconds (default 90, the owner's Q2 answer).
  - **ARCH-09 → APPROVED as E5 with ARCH-08's expiry:** keep the Showrunner's planning half (memory-only rejected); hide the card's candidate beats from the Director while a viewer direction is active (so they return when it expires).
  - **ARCH-01:** implemented; its transcript validation does not pass on the written rule alone (7 card beats against an active direction in five production runs). It is expected to pass with ARCH-08/ARCH-09 as proposed; re-check on the next real-Director run after they land.

## X-09 → RUN-06 — 2026-09-23

Report: `docs/reviews/experiments/X-09.md`. Data: `logs/experiments/admission/` (40 simulated runs).

- **Result.** No arm meets all three criteria (zero stalls; minimum margin ≥ 1 s; steering latency unchanged). Production (buffer 4.7 s) stalled in 5 of 10 runs under a 5 % doubled-call Director tail with adversarial lines. RUN-06's candidate with a 3.5 s buffer removed every stall but raised median steering latency from 12.0 s to 14.7 s and still spent the whole margin (−0.01 s); with 1.5 s and 1.0 s buffers it stalled as often as production. The stalls trace to Director calls at beat ends (a validator re-ask with a doubled draw, 11 s), which admission cannot bound.
- **Proposed disposition.** **RUN-06 → REJECTED as specified** (keep `jit_policy: "current"`, `buffer_seconds: 4.7`). The stall source is the Director's tail at beat ends; the candidates to address it are outside RUN-06 (fewer re-asks, first-clip streaming ARCH-12/X-19, or starting the next beat's plan earlier when a beat is about to end). The deadline policy stays in the code as an off-by-default experiment flag unless the owner wants it removed.
