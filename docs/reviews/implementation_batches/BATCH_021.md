# BATCH_021 — GPU session 1: X-03, X-04, X-05, X-06 and LTX-09's rerun

**Status:** OWNER_REVIEW_REQUIRED (X-03..X-06 RESULT_READY; LTX-09 COMPLETED) · **Date:** 2026-09-23 · **Base:** `f133d64`

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

## Result

GPU session 1 ran on Vast offer 28160519 (H100 80GB HBM3 SXM, $2.668/h, instance 52310745) from 22:44 to 23:52 UTC: 67 minutes, **≈ $3.00**; ≈ $3.68 of the $30 cap spent in total. No instance is left (`vastai show instances`: none). Report: `docs/reviews/experiments/X-03-X-06.md`; proposals: `08_DECISION_UPDATE_PROPOSALS.md`.

| Item | Outcome |
|---|---|
| LTX-09 | **COMPLETED**: 12 of 12 rerun clips and 69 of 69 first-pass clips byte-identical on one server; not across a server restart (recorded) |
| X-05 | RESULT_READY: prose equal, 22 % longer; criterion not met → LTX-01/LTX-06 proposed REJECTED as the default |
| X-06 | RESULT_READY: no shortened anchor holds by clip 12 over three seeds; keep today's text; LTX-14 not triggered |
| X-04 | RESULT_READY: same landing for windows 1/9/17; keep 9 |
| X-03 | RESULT_READY: 153 frames +0.23 s; seam at the codec floor; A/V aligned with padding; both shapes miss wall p95 ≤ 0.85 on this box |
| RUN-13 warm probe | first hardware run: p50 PASS, p95/max FAIL against session A + 3 %; passes RUN-04's absolute gates |

## Files changed

| File | Change |
|---|---|
| `scripts/experiments/gpu_session1.py` | the revision from `AR_REVISION` (the tree on the box has no `.git`); server-side ratios; headers kept whatever their case (`e8d62ad`); a missing experiment folder skipped; window-aware seam measures (`join`, `join_over_local`, overlap aligned to the arm's window) |
| `server/server_defaults.sh` (new), `server/remote_setup.sh`, `server/vast_run.sh`, `server/README.md` | the server's defaults in one file, sourced by the first start and by `restart` (a restart had loaded bf16 at 960×544 uncompiled and run out of memory) |
| `docs/reviews/experiments/X-03-X-06.md` (new), `08_DECISION_UPDATE_PROPOSALS.md`, `08_IMPLEMENTATION_PROGRESS.md`, this report | results, proposals, ledger |

Evaluation data (gitignored): `logs/experiments/gpu1/` (405 clips, `renders.jsonl` per arm, `analysis.json`, `framing*.json`, `determinism.json`, `x04/onset.json`, `figures/`), `logs/probe/h100sxm-fp8-warm30/`, `logs/batch-021/` (server, setup, runner and restart logs; `hardware.txt`; `extra.py`, the by-eye helpers; `ship_and_run.sh`).

No product code, prompt, schema or config changed; no experiment-gated behaviour is enabled.

## Validation

| Check | Result |
|---|---|
| `python -m tests.run` before / after | 114 / 114 passed |
| The runner against the suite's fake server, with lower-case headers as uvicorn sends them (scratch smoke test) | run + analyse; headers, server ratios, rerun equality, revision |
| `bash -n` on the three server scripts; `py_compile` on the runner | pass |
| The fixed `restart` on the box | loaded fp8, compiled, 640×352; warm-up 74 s from the inductor cache |

## Deviations from the plan

- The first X-05 pass lost the server's headers (uvicorn's lower-case names; the smoke test's aiohttp fake kept the case). Fixed, re-rendered (about 6 minutes); the first pass is kept as determinism evidence.
- X-06 ran on three seeds instead of one: the one-seed verdict flipped between seeds (framing shrink on seed 0 only; hair and prints on seeds 1-2). Same experiment, 96 clips, about 8 minutes, at the X-05 replicate count; not a sweep.
- `vast_run.sh restart` fixed inside the batch (about 10 minutes of rental lost to the first attempt).
- The seam measure was corrected during analysis (window-aware).
- The by-eye criteria were judged by the agent from frames; no independent viewers.

## Risks and rollback

Evaluation code and server tooling only. Rollback: revert the commits; the restart fix can be reverted independently (`server/server_defaults.sh` and the three script hunks).

## Blockers and next

- Owner decisions: the proposals for X-02/X-13, X-07/X-08, X-09 and now GPU session 1 (LTX-01/06/02/11/14, LTX-12's shape and the tail headroom).
- Listeners: X-01, X-13's listening half, X-15/X-29.
- GPU session 2 (X-10, X-11, X-12, X-18, X-31, the live run): X-11's RUN-10 flag and X-18's menu need preparation first; the tail headroom found here bears on the live run's RUN-04 gates.

**Recommended next batch:** preparation for GPU session 2 that needs no owner decision (X-12's 16:9 master and arms; X-31's multi-shot arms if the installed pipeline supports native multi-shot; X-18's matched scenes and the 4/6/8 s shapes' warm-up), or implementation of whichever proposals the owner approves.

**Commits:** `ff4524a` (runner on the box), `e8d62ad` (header case), and this batch's close.
