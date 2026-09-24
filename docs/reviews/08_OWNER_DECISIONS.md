# 08 — Owner decisions after the experiments

Decisions the project owner made on the experiment results in `08_DECISION_UPDATE_PROPOSALS.md`. The final decision record (`05_consolidated_decision_record.md`) stays as it was signed; this file is the owner's later word on the rows it names, and the implementation loop acts on it as it acts on an `APPROVED` status in the record. The progress ledger shows each row's record status and the decision below.

## 2026-09-23 — asked in the implementation session, answered by the owner

The questions as asked, and the answers chosen (each was the recommended option).

| # | Question (as asked) | Answer | Rows and new status |
|---|---|---|---|
| D-01 | Story authority (X-07/X-08): approve ARCH-08 (viewer momentum stays; a Showrunner card clears it only if that run was spawned after the line; expires after 90 story seconds) and ARCH-09 (keep the Showrunner's planning half; hide scene-card candidate beats while a viewer direction is active)? | **Approve both.** Implement both as proposed; ARCH-01's transcript check is re-run on the next real-Director run. | ARCH-08 → `APPROVED` (the "momentum stays" branch: scoped reset, 90-s expiry). ARCH-09 → `APPROVED` (arm E5 with ARCH-08's expiry; memory-only rejected). |
| D-02 | Prompt language (GPU session 1): close LTX-01/LTX-06 (keep today's labelled compiler), keep today's anchor text (LTX-02), close LTX-14 (no style LoRA) and LTX-11 (window stays 9)? | **Close all, delete the prose arm.** One compiler; remove `src/story/compiler_prose.py` and its flag and tests. | LTX-01 → `REJECTED`, its arm removed. LTX-06 → `REJECTED` (it lived in the arm). LTX-02 → `REJECTED` (today's `visual_anchor` and `look_short` stay). LTX-14 → `REJECTED` (no LoRA; X-21 not run). LTX-11 → `REJECTED` (the window stays 9 frames). |
| D-03 | Seam design (LTX-12/AUD-02/AUD-03, plus RUN-12): the picture passes (0 duplicate frames, 0.01 % dip), audio pads to the video, the −18 LUFS ceiling works. But X-02's seam-step criterion fails on LTX's clip-to-clip silence, and render time has no headroom. What now? | **Approve; headroom work first.** Approve 153 frames + overlap player + −18 LUFS ceiling; close RUN-12 (no MSE player). Roll it out only after the latency headroom work in D-04 lands. | LTX-12, AUD-02, AUD-03 → `APPROVED` as one design (render 153 frames with the 9-frame overlap kept in the file; the player cuts at n − overlap with an audio crossfade over the overlap; the server pads audio to the video under a −18 LUFS / −1 dBTP ceiling). Rollout (the live config) waits for RUN-14's GPU validation. RUN-12 → `REJECTED` (closed: no MSE player). X-02's seam-step criterion is accepted as unmet. |
| D-04 | Render-time headroom: allow a new row to cut the 1.4 s of CPU encode + client overhead per clip (x264 tuning or an NVENC-capable image, a leaner client path), and reject RUN-06 (deadline-aware line admission; X-09 found no arm that works)? | **New row + reject RUN-06.** The row is drafted, prepared locally, and measured on the next GPU session before the live run. | RUN-14 (new, below) → `APPROVED`. RUN-06 → `REJECTED` (`jit_policy: "current"`, `buffer_seconds: 4.7` stay). |

## New rows

| ID | Decision | Recommended choice | Evidence | Risks / rollback | Depends on | Validation | Scope | Owner status |
|---|---|---|---|---|---|---|---|---|
| RUN-14 | ARCH (runtime). Render-time headroom outside the model | Cut the per-clip time that is not the model: the CPU work between the pipeline's output and the MP4 (float-to-uint8 conversion, colour conversion and x264 encode, 1.1 s encode on GPU session 1's box) and the time between the server's measured stages and the client's receipt (about 0.3 s on localhost). Candidates: convert frames on the GPU, pipe YUV 4:2:0 instead of RGB, tune x264's threading, an NVENC-capable container where Vast allows it, and a leaner response path. Picture and sound unchanged apart from encoder rounding. Prepared and tested locally; measured on the next GPU session with `scripts/latency_probe.py --warm 30` and X-03's chain before the live run. | GPU session 1 (`docs/reviews/experiments/X-03-X-06.md`): generate 3.5-3.7 s, encode 1.0-1.3 s, client overhead 0.29-0.40 s; wall p95 0.848-0.912 against the 0.85 gate. | An encoder change could alter colour or A/V sync; each step behind the server's `LTX2_*` settings. Rollback: the setting. | RUN-05, RUN-13 | Warm probe and X-03's chain on hardware: wall p95 ≤ 0.85 with margin on both shapes; `scripts/seam_check.py` and `scripts/audio_check.py` on the new encode; A/V 0 ms with padding. | `server/ltx2_server.py`, `src/i2v/ltxv.py`, `server/server_defaults.sh` | `APPROVED` (D-04) |

## Not decided

- **AUD-09** (room tone) stays `DEFERRED`; its reopening condition is met (X-02 leaves digital-silence holes), which the proposals noted.
- **X-01** and **X-13**'s listening half still need listeners (AUD-05 stays gated; D-03 fixes the ceiling at −18 LUFS).
- The deadline policy (`jit_policy: "deadline"`) is still in the code as an off-by-default flag; RUN-06's rejection does not say whether to remove it.
