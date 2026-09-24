# 08 — Decision update proposals (owner review required)

Experiments of the final decision record (`docs/reviews/05_consolidated_decision_record.md`) that have reported. Each entry gives the result and a recommended disposition; the owner decides whether a gated row changes status. The decision record is not edited by the implementation loop, and no gated production behaviour has been implemented or enabled. Newest first.

## X-19 → ARCH-12: first-clip streaming — 2026-09-24

Report: `docs/reviews/experiments/X-19.md`.

- **Defer ARCH-12 (the record's "not now").** Over 50 answer calls with today's Director, the streamed first clip closes at 1.99 s (median), against 2.66 s for the whole call: 0.6-0.9 s saved per answer. All 50 streamed replies are valid.
- The change is not small: `BeatPlan`'s field order, a streaming backend, and the engine emitting clip 1 before the beat is whole (so clip 1 needs its own check, since a re-ask cannot take back an emitted clip).
- The film already passes every gate without it. Adopt it only if the buffer is lowered to land lines sooner.

## GPU session 4: RUN-17, LTX-15, RUN-16 — 2026-09-24

Report: `docs/reviews/experiments/session4.md`. RUN-17 and RUN-16 met their own criteria and are adopted (512×288 with strict 16:9 references; gpt-5.4-mini as the Director).

- **LTX-15: keep cuts a story option; the Lego story stays one continuous take.**
  - Written cuts into and out of close-ups happen, and none appear unwritten.
  - But they land 2-4 s into the clip, and a new angle renders as the figure turning.
  - Close-ups repaint the minifigure's face, which the story forbids.
  - Live, some cuts render as dissolves, and a few cut clips degrade.
  - If cuts are wanted later: compile the cut first ("The clip opens on a cut to …"), which is untested, and prefer close-ups on props and hands over faces.
- **A finding, no row yet: the void drifts into a LEGO baseplate floor over long films.** It shows in session 2's film, before today's changes, and in both of today's, earliest in gpt-5.4-mini's. The visual anchor already says "no floor seam". A candidate is a stronger floor clause in the story's `visual_anchor` ("a seamless white floor with no studs, no grid"), tested over a long film.

## RUN-15: plan the next beat earlier (D-14) — 2026-09-24

Report: `docs/reviews/implementation_batches/BATCH_033.md`.

- **Keep `plan_early: true`** (it is on). In the JIT simulation with real runs' Director calls it is neutral under X-10's slow Director and better under session 2's fast one: a fuller queue, and lines landing 1.2 s sooner on average. The cost is some discarded plans (tokens).
- **Its validation's stall half is not met under X-10's tail**, and cannot be: at X-10's call speed the old schedule already started the call at the last clip's emit. X-10's stalls need a shorter call (RUN-16) or the first clip sooner (X-19 → ARCH-12).

## GPU session 3: X-10, X-11, X-12, X-22, X-23, X-31 — 2026-09-24

Report: `docs/reviews/experiments/session3.md`.

- **RUN-10 → APPROVED with `ltxv_restart_recovery: "window"` as the default** (X-11: the scene survives a server restart; recovery +0.4 s). The reference stays the fallback before the first clip.
- **LTX-15 (multi-shot):** controllable on the open model (6/6 requested cuts, 0/12 unrequested); adopting it needs a per-clip cut field for the Director and prompt rules — a product choice.
- **RUN-11's asset swap:** keep the letterboxed square; the supplied master is a wide shot and the film opens on a zoom. The working tree's `legoman.png` is that master (uncommitted); `vast_run.sh run` would ship it.
- **LTX-04:** keep the rules; X-22 saw no artefact reduction. **X-23:** do not adopt the clause.
- **X-10:** renders stable over 300 clips; the three stalls are the Director's latency tail (calls of 6-14 s). Levers outside current rows: first-clip streaming (X-19/ARCH-12), planning the next beat earlier near a beat's end, a larger buffer, a faster Director model.

## After D-01: ARCH-01's remaining gap, and a lost viewer line — 2026-09-23

Report: `docs/reviews/implementation_batches/BATCH_023.md`. Data: `logs/experiments/story-authority-d01/` (eight real-Director runs on X-07/X-08's harness, production code with ARCH-08/ARCH-09).

- **ARCH-01 (still BLOCKED).** With the scoped reset, the expiry and the hidden candidates in, beats that follow a card older than the viewer's active direction fell from 7 (five runs) to 3 (eight runs, two of them), and early-clearing snap-backs from 7 to 2. The three left return to the old card's *goal*, 50-60 s after the viewer's line, with the candidates hidden ("he gets sleepy and tries to build himself a bed" → the captain card). **Proposal:** while a direction is active, show the card's goal as paused in the Director's plan section too (for example "Scene 2 (paused while the viewer's direction is active): goal …"), so nothing in the plan section competes with the direction; the Showrunner's copy unchanged. A prompt-context change of the ARCH-09 kind, validated on the same harness.
- **A failed Director call loses the viewer's line (new; no row covers it).** When the call that answers a line fails (here OpenAI connection timeouts, 2 of 8 runs), the engine holds the picture for one clip and the line is never answered: the viewer's direction silently disappears. **Proposal:** keep the line pending when the answering call fails and answer it at the next plan (a validator re-ask already exists; this is the network-failure path); a test with a backend that fails once. Small, deterministic, in `src/graph/director_engine.py` and `src/eval/runner.py`.

## X-03, X-04, X-05, X-06 → LTX-12's shape, RUN-04, LTX-11, LTX-01, LTX-06, LTX-02, LTX-14 (GPU session 1) — 2026-09-23

Report: `docs/reviews/experiments/X-03-X-06.md`. Data: `logs/experiments/gpu1/` (405 clips, every request and header). One H100 SXM box, 67 minutes, ≈ $3.00 (≈ $3.68 of the $30 cap spent in total).

- **Results.**
  - **X-05:** the caption-prose compiler is equal to today's labelled compiler on identity, framing and action legibility over three seeds, and 22 % *longer* on the Lego blueprint (154 words against 126); on one seed it painted frowning brows on two clips. The criterion ("equal or better … with fewer words") is not met.
  - **X-06:** none of the four shortened anchors (none, style clause, identity invariants, both) is free of drift by clip 12: each adds hair pieces or printed shirts on two of three seeds ("both" also a studded baseplate on one); today's full anchor shows none on six chains. The words the candidates drop ("bare yellow head", "plain red torso") are the ones that keep those parts away.
  - **X-04:** the steered action lands at the same point in windows 1, 9 and 17; 9 is the only window with neither a seam jump nor a raised join floor.
  - **X-03:** 153 frames cost +0.23 s per clip (5.16 s against 4.93 s median) and are slightly cheaper per second of new picture; the overlap cut sits at the codec floor except where the picture breaks (one dissolved breakdance); audio is 31.7 ms (145) and 3.3 ms (153) short of the video and exactly aligned with AUD-03's padding. On this box **both shapes miss p95 ≤ 0.85 on the wall clock** (0.880, 0.912) and pass server-side (0.819, 0.847); RUN-13's warm probe on today's shape: p50 0.813 (pass), p95 0.848 and max 0.857 (fail against session A + 3 %, pass RUN-04's absolute gates). Encode (1.1 s, CPU libx264) and client overhead (0.3 s) are 1.4 s of the 4.9 s.
  - **LTX-09:** reruns reproduce, 81 of 81 clips byte-identical on one server; not across a server restart (recorded).
- **Proposed dispositions.**
  - **LTX-01 and LTX-06 → REJECTED as the default** (the `compiler: prose` flag stays off). Owner's call whether `src/story/compiler_prose.py` stays as an experiment arm; the loop recommends removing it (one compiler) unless a new model or story reopens the comparison.
  - **LTX-02 → close with today's anchor text** (no candidate form holds); a future shortening keeps "bare"/"plain" and is re-tested.
  - **LTX-14 → close** (text holds the style; X-21 not triggered).
  - **LTX-11 → close at 9 frames** (no change).
  - **LTX-12 / AUD-02 / AUD-03:** if the seam design is approved, its render shape is **153 frames** with AUD-03's padding; X-03's latency criterion is not met on this box by either shape, so the seam design should wait until the tail headroom is recovered. The candidates for that are outside the model and outside every current row (the encoder's settings on the box's CPU or an NVENC-capable image; the client's 0.3 s per clip); a new row is needed to work on them. X-02's open question (the seam-step criterion against LTX's clip-to-clip silence) still stands.
  - **RUN-04:** the gates stand; the next live run on a similar box would likely fail p95 on the wall clock. The owner may want the headroom work above before the phase-6 live run.

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
