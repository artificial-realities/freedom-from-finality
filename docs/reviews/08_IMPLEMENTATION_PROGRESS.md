# 08 — Implementation progress ledger

The execution state of every decision in the final decision record. The record itself is immutable evidence and is never edited by the implementation loop; owner statuses below are copied from it verbatim. Completion lives here and in the batch reports under `docs/reviews/implementation_batches/`.

- **Decision record:** `docs/reviews/05_consolidated_decision_record.md` — "FINAL (2026-09-22)"
- **Record SHA-256:** `1d2674087649f77943169f5fcdda88c0a6eac07372b8526ddf103f658a2e8054` (the working-tree file at ledger creation; the FINAL edit is uncommitted on top of HEAD `42ddda8`)
- **Ledger created:** 2026-09-22, session 1 of the Prompt 08 loop
- **Latest completed batch:** BATCH_004 (RUN-13 part c), 2026-09-22
- **Batch in progress:** none
- **GPU spend:** $0.00 of the $30.00 authorised (record §2, Q8)
- **Owner statuses (from the record):** 46 `APPROVED`, 18 `EXPERIMENT_REQUIRED`, 2 `DEFERRED`, 1 `REJECTED`, 0 `PENDING` — re-counted from the register at creation (67 rows)

**Execution states.** `NOT_STARTED` · `IN_PROGRESS` · `COMPLETED` (implemented and its required validation passed) · `BLOCKED` (cannot proceed or cannot be validated; the note says why) · `RESULT_READY` (experiment finished, owner review pending; never `COMPLETED` before owner approval) · `NOT_APPLICABLE` (rejected, deferred, or approved keep-as-is with scope "none").

**Phase column.** From record §2. `N*` marks an approved row that §2 does not place; it is slotted by its dependencies and scope, and the note says why. `3→4` = experiment-gated (run in phase 3, implement in phase 4 only after an owner status change). `—` = no work.

## Decisions

| ID | Owner status | Phase | Execution | Depends on | Batch | Validation evidence | Note |
|---|---|---|---|---|---|---|---|
| ARCH-01 | APPROVED | 1* | NOT_STARTED | none | — | — | Unplaced in §2; placed with phase 1 (prompts/docs, no code beyond ARCH-02..09). Validation needs a live-run transcript review. |
| ARCH-02 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| ARCH-03 | APPROVED | 1 | NOT_STARTED | ARCH-02 | — | — |  |
| ARCH-04 | APPROVED | 0* | NOT_STARTED | none | — | — | Unplaced in §2; docs only — group with CLEAN-04 docs batch. |
| ARCH-05 | APPROVED | 1 | NOT_STARTED | ARCH-03 | — | — |  |
| ARCH-06 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| ARCH-07 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| ARCH-08 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | X-07, ARCH-03 | — | — | Gated by X-07 (LLM-only, headless, scripted lines); ARCH-03 first. |
| ARCH-09 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | X-08, ARCH-02, ARCH-03 | — | — | Gated by X-08 (LLM-only, headless); ARCH-02/03 first. |
| ARCH-10 | APPROVED | 2+* | NOT_STARTED | RUN-01 | — | — | Unplaced in §2; depends on RUN-01 (phase 2). |
| ARCH-11 | APPROVED | 1* | NOT_STARTED | none | — | — | Unplaced in §2; no deps; prompt/context gate change. Validation reads Director latency on answer calls (timing log, RUN-05). |
| ARCH-12 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | X-19 | — | — | Gated by X-19 (optional experiment, required before this row). |
| ARCH-13 | APPROVED | — | NOT_APPLICABLE | ARCH-02, ARCH-03 | — | — | Approved keep-as-is (no change to the card; scope none). Cards exist in the engine; lifecycle fixes are ARCH-02/03. |
| ARCH-14 | APPROVED | 0* | NOT_STARTED | none | — | — | Unplaced in §2; docs only — group with CLEAN-04. |
| ARCH-15 | APPROVED | — | NOT_APPLICABLE | none | — | — | Approved keep-as-is (forced scene closure unchanged; scope none). Verified present: `DirectorEngine.plan` forces `scene_done` past `scene_seconds`. |
| ARCH-16 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| ARCH-17 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| ARCH-18 | APPROVED | 0* | NOT_STARTED | none | — | — | Unplaced in §2; docs only — group with CLEAN-04. |
| ARCH-19 | APPROVED | 1 | NOT_STARTED | LTX-05 for voice fields | — | — | Large blueprint/world/validator change; LTX-05 first for voice fields. Full validation includes X-26 (post-implementation). |
| LTX-01 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | RUN-01, LTX-02, RUN-13 (replay harness) | — | — | Gated by X-05 (GPU). Permitted prep: comparison compiler behind an off-by-default flag, after RUN-01 and RUN-13 (replay harness). Dependency cycle with LTX-02 in the record (each lists the other); X-06 runs on the prose compiler, so the LTX-01 arm comes first. |
| LTX-02 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | LTX-01 | — | — | Gated by X-06 (GPU). Candidate blueprint forms prepared only after the LTX-01 arm exists (see LTX-01 note). |
| LTX-03 | APPROVED | 0* | NOT_STARTED | CLEAN-08 if the old compiler remains active | — | — | Unplaced in §2; compiler bug fix, pairs with CLEAN-08 (camera-line grammar) in one compiler batch. |
| LTX-04 | APPROVED | 2+* | NOT_STARTED | RUN-01 | — | — | Unplaced in §2; depends on RUN-01. X-22 is post-implementation validation. |
| LTX-05 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| LTX-06 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | LTX-01 | — | — | Part of the LTX-01 arm; gated by X-05. |
| LTX-07 | REJECTED | — | NOT_APPLICABLE | none | — | — | Rejected by owner; keep current behaviour (enhancer off); do not run X-20. |
| LTX-08 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| LTX-09 | APPROVED | 0 | NOT_STARTED | none | — | — | Validation column is "X-05 reruns reproduce" (GPU); locally only the seed derivation can be tested. |
| LTX-10 | APPROVED | — | NOT_APPLICABLE | none | — | — | Approved keep-as-is (scope none). Verified: `configs/director.live.yaml` has `ltxv_continuity_strength: 1.0`. |
| LTX-11 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | LTX-12, RUN-01 | — | — | Gated by X-04 (GPU); window stays 9 until then. |
| LTX-12 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | RUN-01, AUD-02, AUD-03 | — | — | One design with AUD-02/AUD-03; gated by X-02 (local) and X-03 (GPU); RUN-01 first. |
| LTX-13 | APPROVED | — | NOT_APPLICABLE | none | — | — | Approved keep-as-is (scope none). Verified: `configs/director.live.yaml` has `ltxv_anchor_strength: 0.0`. |
| LTX-14 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | X-06 | — | — | Gated by X-21, which runs only if X-06 fails. |
| LTX-15 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | RUN-05, LTX-09 | — | — | Gated by X-31 (GPU); needs RUN-05 and LTX-09 first. |
| RUN-01 | APPROVED | 2 | NOT_STARTED | none (first in the roadmap) | — | — | First row of phase 2; blocks LTX-12, AUD-02, AUD-04 wording, RUN-02, RUN-03, RUN-08, ARCH-10, LTX-04. |
| RUN-02 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | RUN-01, RUN-05, RUN-08, LTX-09 | — | — | Gated by X-18 (GPU); needs RUN-01, RUN-05, RUN-08, LTX-09. |
| RUN-03 | APPROVED | 2+* | NOT_STARTED | RUN-01 | — | — | Unplaced in §2; depends on RUN-01. |
| RUN-04 | APPROVED | 6 | NOT_STARTED | RUN-05, RUN-13 | — | — | Not selected (RUN-13 incomplete). BATCH_002 wrote RUN-04's threshold values into `benchmarks/runtime/baseline.json` `gates` because RUN-13's `bench_report` must fail on them; RUN-04's own validation (bench_report on the next live run, GPU) remains. |
| RUN-05 | APPROVED | 0 | COMPLETED | none | BATCH_001 | `python -m tests.run` 32/32 (7 new in `tests/test_timing.py`); headless run of `configs/director.yaml --player web` (real Director) wrote `logs/batch-001/real-live/sessions/director_live_20260922_190413/timing.jsonl` (50 records); terminal loop and headless-Chromium replay run also wrote it, 7 page beacons received | `src/util/timing_log.py`; records listed in BATCH_001. `revision` is null on the GPU box (no `.git` in the shipped tree): for RUN-13. |
| RUN-06 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | RUN-05, X-09, ARCH-06 | — | — | Gated by X-09 (local Monte Carlo + replay loop); needs RUN-05 and ARCH-06. |
| RUN-07 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| RUN-08 | APPROVED | 2 | NOT_STARTED | RUN-01 | — | — |  |
| RUN-09 | APPROVED | 5 | NOT_STARTED | RUN-07 | — | — | Phase 5; depends on RUN-07. |
| RUN-10 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | RUN-01 | — | — | Gated by X-11 (GPU); RUN-01 first. |
| RUN-11 | APPROVED | 1 | NOT_STARTED | none | — | — | Static checks + fallback now; the 16:9 asset swap stays gated by X-12 (GPU). |
| RUN-12 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | X-02, LTX-12 | — | — | Built only if X-02 leaves a swap gate unmet. |
| RUN-13 | APPROVED | 0 | IN_PROGRESS | RUN-05 | BATCH_002, BATCH_003, BATCH_004 | Part a: `python -m tests.run` 42/42 (10 new in `tests/test_bench.py`); `bench_report` on the BATCH_001 logs (`logs/batch-002/bench-*.txt`): the real headless run fails `stalls` and `min_queue_margin_s` as expected. Part b: `python -m tests.run` 45/45 (3 new in `tests/test_jit.py`); `test_jit` 5/5 repeat runs and under full CPU load; five scheduler mutations of `runner.py` all caught (`logs/batch-003/mutations.txt`). Part c: `python -m tests.run` 53/53 (8 new in `tests/test_checks.py`); `seam_check` and `audio_check` on the 2026-09-18 clips reproduce review 03 §3 and review 04 §9.1 (`logs/batch-004/`: dip 24.9 %, swap p95 2.1 ms, 6 reloads with two queued, 3 fades with the outgoing element replaced; 16 clips above −18 LUFS, 14 over −1 dBTP, narrated-to-next 20.8 LU); canvas dip against screenshots within 0.3 % | Done: a = `benchmarks/runtime/baseline.json`, `scripts/bench_report.py`, README table, CLAUDE.md pointer; b = `tests/jit_sim.py` + `tests/test_jit.py` (live config at 25×, baseline draws, adversarial lines, zero stalls; the ≥ 1 s margin gate is not met under a line 0.3 s before a fire, by design of the current policy: X-09/RUN-06). c = `scripts/seam_check.py` (files + headless Chromium), `scripts/audio_check.py` (clips + played mix), `tests/test_checks.py`. Remaining: d = compiler replay harness, `latency_probe --warm 30` with +3 % tolerance (GPU-validated only). |
| AUD-01 | APPROVED | — | NOT_STARTED | none | — | — | Approved direction with no direct code scope; realised through AUD-04/05/06/07. Validation needs X-01 (listeners) and X-15/X-29 (GPU). |
| AUD-02 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | LTX-12, RUN-01, AUD-03 | — | — | One design with LTX-12/AUD-03; gated by X-02. |
| AUD-03 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | LTX-12, AUD-02 | — | — | One design with LTX-12/AUD-02; value from X-13 (local), rollout with X-02. |
| AUD-04 | APPROVED | 1* | NOT_STARTED | none | — | — | Unplaced in §2; no deps, but its validation needs the JIT simulation test (RUN-13). Not on the §5 safe-cleanup list (semantic change). |
| AUD-05 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | X-01, AUD-04 | — | — | Gated by X-01 (blind listening, >=3 listeners; owner must supply listeners); AUD-04 first. |
| AUD-06 | APPROVED | 1 | NOT_STARTED | none | — | — |  |
| AUD-07 | EXPERIMENT_REQUIRED | 3→4 | NOT_STARTED | AUD-06, LTX-05, RUN-01 | — | — | Gated by X-15 and X-29 (GPU + listeners); AUD-06, LTX-05, RUN-01 first. |
| AUD-08 | APPROVED | — | NOT_APPLICABLE | none | — | — | Approved keep-as-is (no music system in the MVP; scope none). No music code exists. |
| AUD-09 | DEFERRED | backlog | NOT_APPLICABLE | AUD-02 | — | — | Deferred by owner (future backlog); no work. |
| AUD-10 | DEFERRED | backlog | NOT_APPLICABLE | none | — | — | Deferred by owner (future backlog); no work. |
| CLEAN-01 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| CLEAN-02 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| CLEAN-03 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| CLEAN-04 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| CLEAN-05 | APPROVED | 2 | NOT_STARTED | RUN-08 | — | — | Listed in phase 0 but depends on RUN-08 (phase 2). |
| CLEAN-06 | APPROVED | 2 | NOT_STARTED | RUN-01, AUD-06 | — | — | Listed in phase 0 but depends on RUN-01 and AUD-06. |
| CLEAN-07 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| CLEAN-08 | APPROVED | 0 | NOT_STARTED | none | — | — | Fix in the active compiler now; pairs with LTX-03. |
| CLEAN-09 | APPROVED | 0 | NOT_STARTED | none | — | — |  |
| CLEAN-10 | APPROVED | 0 | NOT_STARTED | none | — | — | Docs-only note about the 01 review header; the reviews are untracked user files. |

## Experiments

States as for decisions. Only experiments named in the record are listed; X-20, X-25 and X-28 were removed by the owner and must not run.

| ID | Kind | Gates | Prerequisites before it can run | Needs | State | GPU $ | Result |
|---|---|---|---|---|---|---|---|
| X-01 | blocking | AUD-05 | Session A clips (local); blind pairs prepared | ≥ 3 human listeners (owner) | NOT_STARTED | 0 | — |
| X-02 | blocking | AUD-02, LTX-12, RUN-12 | RUN-01; AUD-02/AUD-03/LTX-12 candidate as an off-by-default variant; `scripts/seam_check.py` and `scripts/audio_check.py --policy overlap` (done, BATCH_004) | local, headless Chromium | NOT_STARTED | 0 | — |
| X-03 | blocking | LTX-12 shape; RUN-04 | RUN-01, RUN-05, RUN-13 (`latency_probe --warm`, `bench_report`), LTX-09 | GPU (~30 min), session 1 | NOT_STARTED | 0 | — |
| X-04 | blocking | LTX-11 | as X-03; replayable Director outputs | GPU (~45 min), session 1 | NOT_STARTED | 0 | — |
| X-05 | blocking | LTX-01 flag default | LTX-01 arm (flag off), RUN-13 compiler replay harness, LTX-09 | GPU (~45 min), session 1 | NOT_STARTED | 0 | — |
| X-06 | blocking | LTX-02 text; LTX-14 | X-05 arm; LTX-02 candidate forms | GPU (~45 min), session 1 | NOT_STARTED | 0 | — |
| X-07 | blocking | ARCH-08 | ARCH-03; RUN-05; `scripts/steer.py` | OpenAI calls only | NOT_STARTED | 0 | — |
| X-08 | blocking | ARCH-09 | ARCH-02, ARCH-03; RUN-05 | OpenAI calls only | NOT_STARTED | 0 | — |
| X-09 | blocking | RUN-06 | RUN-05 (done), RUN-13 JIT simulation (done: `tests/jit_sim.py`), ARCH-06 | local | NOT_STARTED | 0 | — |
| X-10 | blocking (long-film trust) | RUN-04 for long films | phases 0-5 | GPU (~40 min), session 2 | NOT_STARTED | 0 | — |
| X-11 | blocking | RUN-10 flag default | RUN-01; RUN-10 behind a flag | GPU (~20 min), session 2 | NOT_STARTED | 0 | — |
| X-12 | blocking | RUN-11 asset swap | RUN-11 static checks; a 16:9 master | GPU (~10 min), session 2 | NOT_STARTED | 0 | — |
| X-13 | blocking | AUD-03 value | session A clips (local); `scripts/audio_check.py` (done, BATCH_004; the ceiling arm needs the AUD-03 candidate encode) | local + listening H2 | NOT_STARTED | 0 | — |
| X-14 | conditional | AUD-09 | only if X-02 leaves audible gaps and AUD-09 is reopened | local + listening | NOT_STARTED | 0 | — |
| X-15 | blocking | AUD-07; informs RUN-02 | X-18 candidate durations; LTX-05; AUD-06 | GPU (45-60 min) + listeners | NOT_STARTED | 0 | — |
| X-18 | blocking | RUN-02 | RUN-01, RUN-05, RUN-08, LTX-09; multi-shape warm-up | GPU (60-90 min) | NOT_STARTED | 0 | — |
| X-29 | blocking | AUD-07; validates LTX-05 voice use | LTX-05; a synthetic two-voice test story | GPU (30-45 min) + listeners | NOT_STARTED | 0 | — |
| X-31 | blocking | LTX-15 | RUN-05, LTX-09 | GPU (45-60 min) | NOT_STARTED | 0 | — |
| X-22 | post-implementation | LTX-04 | LTX-04 landed | GPU | NOT_STARTED | 0 | — |
| X-26 | post-implementation | ARCH-19 | ARCH-19 landed | LLM (+ GPU for identity) | NOT_STARTED | 0 | — |
| X-23 | post-implementation | "no vocal sound" clause | — | GPU | NOT_STARTED | 0 | — |
| Live run | post-implementation | RUN-04 gates | phases 0-5 | GPU, session 2 | NOT_STARTED | 0 | — |
| X-16, X-17, X-19, X-21, X-24, X-27, X-30 | optional / later | X-19 → ARCH-12; X-21 only if X-06 fails; X-27 only if X-18 selects a menu | — | varies | NOT_STARTED | 0 | — |

## GPU spend

| Date | Session | Box | Experiments | Minutes | Cost | Running total |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | $0.00 of $30.00 |

## Batches

| Batch | Date | Decision IDs | Outcome | Report |
|---|---|---|---|---|
| BATCH_001 | 2026-09-22 | RUN-05 | COMPLETED (commit `b5e6d3d`) | `implementation_batches/BATCH_001.md` |
| BATCH_002 | 2026-09-22 | RUN-13 (part a) | COMPLETED (row stays IN_PROGRESS; commit `e77c787`) | `implementation_batches/BATCH_002.md` |
| BATCH_003 | 2026-09-22 | RUN-13 (part b) | COMPLETED (row stays IN_PROGRESS; commit `a14c44a`) | `implementation_batches/BATCH_003.md` |
| BATCH_004 | 2026-09-22 | RUN-13 (part c) | COMPLETED (row stays IN_PROGRESS) | `implementation_batches/BATCH_004.md` |

## Reconciliation notes

- 2026-09-22 (BATCH_004 close): RUN-13 part c validated; the row stays `IN_PROGRESS` for part d. X-02's and X-13's measurement tools now exist. Recorded as evidence for the experiment-gated seam rows, not as a status change: today's player shows a 24.9 % crossfade dip at every seam, reloads on `playing` for both clips whenever two are queued (review 03 §3 2c), and replaces the fading element with the next prefetch when a clip is queued inside the fade (2d), an 85 % one-frame blink; the mix has a 21.5 dB median seam step under the fade-in and 36 dB at the raw join.
- 2026-09-22 (session 4 start): record SHA-256 unchanged; `python -m tests.run` 45/45. The owner committed BATCH_003 as `a14c44a`; recorded in the Batches table. BATCH_004 selected: RUN-13 part c (seam and audio checks), local material and tooling available; part d waits (its `latency_probe --warm` needs a GPU).
- 2026-09-22 (BATCH_003 close): RUN-13 part b validated; the row stays `IN_PROGRESS` for parts c and d. The JIT simulation (the X-09 prerequisite) exists; X-09 still waits for ARCH-06. Recorded for RUN-06/X-09: under the current policy a line 0.3 s before a fire, answered by a ~4 s Director call, leaves ~1.0 s of queue margin (the RUN-04 gate's floor).
- 2026-09-22 (session 3 start): record SHA-256 unchanged; `python -m tests.run` 42/42. The owner committed BATCH_002 as `e77c787` (with the ledger and BATCH_001 report updates); recorded here and in the Batches table. BATCH_003 selected: RUN-13 part b, as BATCH_002 recommended.
- 2026-09-22 (BATCH_002 close): RUN-13 part a validated; the row stays `IN_PROGRESS` for parts b-d. RUN-04's thresholds now exist in the baseline file as `bench_report`'s input; RUN-04 is not marked started.
- 2026-09-22 (session 2 start): repository, record SHA-256 and ledger agree; BATCH_001's evidence is present under `logs/batch-001/` and `python -m tests.run` is 32/32. The owner committed BATCH_001 as `b5e6d3d` after the session; recorded here and in its report.
- 2026-09-22 (BATCH_001 close): RUN-05 validated. The real headless validation run recorded one 0.40 s stall at clip 3 caused by a 5.15 s answering Director call (default tier): evidence for RUN-06 / X-09, not a regression. Pre-existing pyflakes finding (`runner.py` `_plan`'s unused `nonlocal state`) left as is; it is not covered by any row.
- 2026-09-22 (ledger creation): no earlier ledger or batch reports existed. Keep-as-is rows with scope "none" (ARCH-13, ARCH-15, LTX-10, LTX-13, AUD-08) are `NOT_APPLICABLE`; the configs were checked for LTX-10 and LTX-13. The record gives LTX-01 and LTX-02 as each other's dependency; the resolution in the LTX-01 note follows the record's own experiment order (X-06 runs on the prose compiler).
