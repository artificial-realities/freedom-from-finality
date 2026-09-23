# BATCH_021 — GPU session 1: X-03, X-04, X-05, X-06 and LTX-09's rerun

**Status:** IN_PROGRESS · **Date:** 2026-09-23 · **Base:** `f133d64`

## Plan

**Purpose.** Run GPU session 1 of the decision record (§2 "GPU sessions", Q8) as prepared in BATCH_020: the blocking experiments X-03, X-04, X-05 and X-06 on one warmed LTX-2.5 server, plus the validation LTX-09 is waiting for ("X-05 reruns reproduce"). BATCH_020's rental could not be reached from this machine's network; a port test at 18:40 local (portquiz.net on 40817, 28034, 8080) now passes.

**Decision IDs and experiments.**

| Experiment | Gates (record §4.1) | Arms (`logs/experiments/gpu1/plan.json`) | Criterion (record) |
|---|---|---|---|
| X-03 | LTX-12's shape; RUN-04 | 145 vs 153 frames, segments 1-30, window 9, one derived seed | render p95 ≤ 0.85, max ≤ 0.92; no frame-difference spike at the seam above intra-clip motion; A/V aligned |
| X-04 | LTX-11 | windows 1, 9, 17 on segments 17-28 (the steering line lands on the 4th clip) | earliest landing of the steered action with no seam jump and no identity loss |
| X-05 | LTX-01 flag default (with LTX-06's wording) | production vs prose compiler, segments 1-12, seeds 0/1/2 | prose equal or better on identity and framing with fewer words; actions still legible |
| X-06 | LTX-02 final text; LTX-14 | none / style / identity / both, prose compiler, segments 1-12 | the smallest arm with no drift by clip 12 |
| LTX-09 rerun | LTX-09's validation | x05 labels seed 0 rendered twice | reruns reproduce |
| X-03 A/V | AUD-03's padding (X-03's "A/V aligned") | six clips of each shape after a restart with `LTX2_AUDIO_CEILING_LUFS=-18` | audio and video the same length |

Plus `scripts/latency_probe.py --warm 30` on the default server: RUN-13's warm probe has never run on hardware (BATCH_005).

**Dependencies.** All satisfied: RUN-01, RUN-05, RUN-08, RUN-13, LTX-09 (code), LTX-01/LTX-02/LTX-06 preparation (BATCH_018, off by default), the replay harness and session A's replay file (BATCH_005).

**What runs where.** The renderer (`scripts/experiments/gpu_session1.py run`) runs **on the GPU box**, next to the server, as the live film does (`vast_run.sh run`): the MP4 through the ssh tunnel costs 1.5-4.6 s per clip and would inflate X-03's render ratios against a baseline measured with the client on the box. The tree is shipped with `git archive` of the working tree; the prepared arms (`logs/experiments/gpu1/`, gitignored) with rsync; results are pulled back and analysed locally.

**Code touched.** Evaluation code only: `scripts/experiments/gpu_session1.py` records the revision from `AR_REVISION` when the tree has no `.git`, and its analysis adds the server-side ratio (`X-Latency-S` + `X-Encode-S` over delivered seconds) beside the wall-clock one. No product code, config or prompt changes; nothing experiment-gated is enabled in production.

**Budget.** ≈ $0.68 of $30 spent. Expected: setup 15-20 min, 240 clips at about 5-6 s, a restart and 12 clips, the probe: about 60-75 minutes on an H100 SXM/NVL at about $2-3/h, so $2.5-4. Stop rule (record §2): terminate right after the planned work; stop on repeated infrastructure failures.

**Risks.** Byte-identical reruns may fail under fp8 + torch.compile even with a fixed seed (non-deterministic kernels); the analysis also reports the per-clip pixel difference against a different seed's chain for scale. The by-eye criteria (action legibility, identity, steered-action landing) are judged from contact sheets and frames, and the report says so.

**Rollback.** Evaluation-only; revert the runner edit. The instance is destroyed at the end in any case.
