# GPU session 3 — X-10, X-11, X-12, X-22, X-23, X-31

**Result:** all six RESULT_READY · **Run:** 2026-09-24, 13:40-14:49 UTC · **Code:** `8e024b1` (BATCH_029-031) · **Box:** Vast H100 NVL (Virginia, AMD EPYC 9V84, 40 effective cores), $3.08/h, ≈ $3.55 · **Settings:** the live ones (153 frames, frames on the GPU, YUV piped, 8 x264 threads, −18 LUFS)

X-26's GPU half did not run: the lighthouse fixture has no reference image. By-eye criteria were judged by the implementing agent from frames (no viewers, D-10).

## X-10 — a 30-minute film (RUN-04 for long films)

300 clips of the Lego story on the live config; thirty scripted lines, one every ten segments, in rotation 0.3 s before an announced fire, 0.3 s after one, and 3 s into a segment (`logs/live-box/x10/`).

| Gate | Value | Result |
|---|---|---|
| Render ratio p50 / p95 / max | 0.750 / 0.756 / 0.766 (NVL; SXM baseline 0.669 / 0.689 / 0.696) | PASS |
| **Render trend** | **−0.013 ms per clip over 297 warm renders** (sd 34 ms) | **PASS** |
| Mid-film compiles | 0 | PASS |
| Stalls | **3** (turns 100, 189, 271; 5.41 s held) | FAIL |
| Minimum queue margin | −3.29 s | FAIL |
| Steering p50 / p95 | 14.9 / 15.5 s | FAIL / PASS |
| First picture | 16.0 s | PASS |
| Lines | 30 received, 29 landed, 0 dropped (the last, sent at segment 298, would have landed after the film's end) | — |

- **Renders are stable over half an hour**: no drift, no compile, no failure in 300 renders.
- **Every stall is the Director's.** Turn 100: a line 0.3 s before a fire, answered in 6.2 s against the 4.7-s buffer (1.2 s late). Turn 189: a beat-end call of 11.6 s with no line (0.9 s). Turn 271: a line that cancelled a beat-end call and was answered in 13.8 s (3.3 s). The Director was slower than in session 2 (plan mean 4.68 s against 2.89 s, p95 7.0 s, max 13.8 s; the same model and priority tier, run in US morning hours instead of overnight). X-09 predicted this: stalls come from the Director's tail at beat ends, which line admission cannot bound.
- **Steering p50** reflects the script: two thirds of the lines were placed against a fire; a line just after a fire lands three clips later by design (≈ 15 s), one 3 s into a clip two later (9.5-10.7 s).

## X-11 — restart recovery (RUN-10's flag default)

Two arms, session A's segments 45-60 (the princess and the crown on screen) at 153 frames, the server killed after clip 10 and restarted (`logs/experiments/x11/`).

| | Reference re-seed (today) | Window restore (RUN-10's arm) |
|---|---|---|
| Picture jump at the restart (clip 10's last aired frame → clip 11's first) | **66.1** (other joins 0.57) | **1.66** (other joins 1.12) |
| On-screen content before → after (non-white area) | 0.32 → 0.12 | 0.274 → 0.275 |
| Server restart (weights from cache, 153-frame compile) | 48 s | 42 s |
| Recovery clip | 4.62 s | 5.00 s (+0.4 s to send nine frames; normal 4.53 s) |

By eye (`x11/*/sheet_9-13.png`): after the re-seed the princess is gone and Lego Man is the reference figure again, with the crown suddenly on his own head; after the window restore the scene continues as it was. **Criterion met** (costume and position persist; recovery bounded). The restart itself still takes 42-48 s, during which a live film waits either way.

## X-12 — a 16:9 master (RUN-11's asset swap)

The owner's master (1672 × 941, the figure at 35 % of the frame height) against the letterboxed square (75 %), two seeds, six clips (`gpu3/x12/`). Identity is the same. The master is a wide composition while the opening prompt asks for a medium shot: in both seeds the model zooms in over the first two seconds, a visible jolt at the start of the film; the square opens at medium and stays. After the first clip the arms look alike. **Framing is worse at the start with this master**; a master composed like the opening (the figure at about three quarters of the height) would be the fair test.

## X-22 — LTX-04's renderer rules (post-implementation)

Ten wild lines, each answered by the real Director with and without the "Writing for the video model" section, both rendered with the same seeds (`gpu3/x22/pair-NN.png`). All 20 answers absorbed the line; with the rules the Director writes more cautious action ("tries to", "awkwardly"). **By eye the artefacts are the same in both arms** on the lines checked: blurred flips and handstands (the breakdance, the backflip), a detached second head (the head tricks), a white-out frame (the ceiling walk). Requests honoured in both. One seed.

## X-23 — the "makes no vocal sound" clause (post-implementation)

Session A's segments 1-20 with and without "Lego Man makes no vocal sound." on the 15 clips without speech (`gpu3/x23/`). faster-whisper hears 12 words in 9 of those clips without the clause and 15 words in 9 with it (short fragments on ambience); the clause arm is 2.7 dB quieter (median −42.8 against −40.1 dB) and spectrally a little more tonal (flatness 0.016 against 0.037). **Nothing that the clause is for changes**: no fewer vocalisations heard.

## X-31 — native multi-shot (LTX-15)

Three scenes, each as one take and as a paragraph naming its cuts in prose (LTX-2.5's documented convention), two seeds, each followed by a plain continuing clip (`gpu3/x31/`). The automatic cut count (frame-difference spikes) under-counted: by eye every requested cut happened (**6 of 6**: the ball scene's hard cut to the close-up and back, the wave scene's wide to close, the walk scene's side view to behind him), and **no plain clip cut** on its own (12 of 12, the detector's one flag being a fast head turn). Each continuing clip picked up the last shot after the cut. Two continuing clips drifted in identity (a hair piece, a printed torso) in both arms alike. Latency is unchanged (the same frames). The record's viewer-preference part was not run (D-10).

## Recommended dispositions (for the owner)

- **RUN-10 → APPROVED: restore from the window by default** (`ltxv_restart_recovery: "window"`), the reference only before the first clip; the Director told when a re-seed happens anyway.
- **LTX-15:** multi-shot is controllable on the open model in this test; adopting it means a new per-clip field for the Director (a requested cut and the new framing) and prompt rules: a product decision for the owner, not an adoption by default.
- **RUN-11's asset swap (X-12): keep the letterboxed square** for now; the master as supplied opens on a zoom. Note: `stories/lego-void/legoman.png` in the working tree is the master (uncommitted), and `server/vast_run.sh run` ships the working tree, so a live run started from it would open on the master.
- **LTX-04 / X-22:** keep the rules (harmless, requests still honoured); no measured artefact reduction.
- **X-23:** do not adopt the clause.
- **X-10:** renders are trusted for long films; the schedule is not, because of the Director's latency tail. Candidates, each outside the current rows: first-clip streaming (X-19 → ARCH-12, the record's largest remaining latency lever), starting the next beat's plan earlier when a beat is about to end, a larger buffer (costs steering latency), or a faster Director model.
