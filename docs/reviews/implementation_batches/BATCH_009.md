# BATCH_009 — The render client: derived seeds, the reference checked at load and at startup (LTX-09, RUN-11)

**Status:** COMPLETED (RUN-11) · LTX-09 implemented, its GPU validation pending · **Date:** 2026-09-23 · **Base:** `608d7fc` plus the owner's uncommitted FINAL decision record

From this session on the owner asked for larger batches and commits without asking ("speed up … commit without me"); each batch is still planned, validated and reported, and committed on its own.

## Plan

**Why this batch.** LTX-09 is phase 0's last open row. RUN-11 is a phase-1 row with no dependency in the same subsystem (the render client's request and its reference image: `src/i2v/ltxv.py`, `models/story.py`, the runner's backend construction). One validation boundary: the i2v and story loader tests, plus the full suite.

**Decision IDs.**
- **LTX-09** — The client sends `seed = hash(key, segment)`; the key is the session id, or `ltxv_seed_key` to rerun an earlier run's noise. Never a fixed seed (the `ltxv_seed` field goes), never random when a key exists.
- **RUN-11** — Static checks now, the asset swap stays gated by X-12: the image exists and decodes and the protagonist's `reference_pose` is non-empty (at story load); against the server's render size (at run start): within 5 % of the render aspect → cover-resize; outside → `pad` letterboxes with a warning (plus a warning when the border is too uneven for the fill), `strict` refuses; smaller than the render size → warning.

**Files.** `src/i2v/ltxv.py`, `src/i2v/base.py`, `src/i2v/__init__.py`, `src/models/config.py`, `src/models/story.py`, `src/eval/runner.py`, tests, `docs/ARCHITECTURE.md`, `server/README.md`, `CLAUDE.md`.

**Risks / rollback.** A config that sets `ltxv_seed` loses it silently (the field is ignored; no config in the repo sets it). A story whose image is missing now fails at load instead of at the first render. Rollback: revert the commit.

**Validation required.** LTX-09: the seed derivation and the payload offline; "X-05 reruns reproduce" needs the GPU. RUN-11: loader tests. Full suite.

**Baseline.** `python -m tests.run` at `608d7fc`: 64 passed.

## Result

### Files changed

| File | Change |
|---|---|
| `src/i2v/ltxv.py` | `derive_seed(key, segment)`: the first 4 bytes of SHA-256 of `"{key}:{segment}"`, stable across processes. `LtxvI2V(seed_key=…)` replaces `seed=`; the payload carries the derived seed and `last_render["seed"]` records it (so `render_done.server.seed` is in the timing log). `fit_reference(path, w, h, fit, fill)` → (PNG bytes, warnings): cover-resize within `ASPECT_TOLERANCE` (5 %), otherwise letterbox with a warning (`pad`) or `ReferenceRefused` (`strict`); `border_sd` warns when the border is too uneven for the fill (sd > 12); small images warned. `check_reference(path)`: the startup check against the probed size, returned for the timing log; an unreachable server leaves the check to the first render. `reference_fit` is validated (`pad`, `strict`, `none`). |
| `src/i2v/base.py` | `I2VBackend.check_reference` (default: nothing to check). |
| `src/i2v/__init__.py` | `seed_key` passed through. |
| `src/models/config.py` | `ltxv_seed` (a fixed seed) removed; `ltxv_seed_key: ""` (= the session id). The `i2v_reference_fit` comment describes cover / pad / strict / none. |
| `src/models/story.py` | `Story.check_reference()`, called by `from_json`: the seed image exists and decodes; the first character has a `reference_pose`. |
| `src/eval/runner.py` | `_maybe_i2v(config, log_dir, session_id)` keys the seeds; `_check_reference` runs before anything renders in both loops and writes a `reference` record to the timing log (a strict refusal stops the run there). |
| `tests/test_i2v.py` | Five new tests (below). |
| `tests/test_story.py` | The old-blueprint test copies the image into its temporary story folder (the loader now requires it). |
| `docs/ARCHITECTURE.md`, `server/README.md`, `CLAUDE.md` | The seed and the two reference checks. |

### Behaviour and contract

- Every render request carries `seed` = `derive_seed(ltxv_seed_key or session_id, segment)`. The server already honoured `seed` (it drew a random one when absent); nothing changes server-side.
- The Lego reference (380×381) is 45 % off the 640×352 render aspect, so it is still letterboxed, now with a logged warning and a `reference` record (`fit: pad`); at 960×544 it also warns that the image is smaller than the subject area. The 16:9 master that would switch it to cover-resize is X-12's asset and is not swapped here.

### Validation

| Check | Command | Result |
|---|---|---|
| New tests | `python -m tests.run test_i2v` | 9 passed: `test_render_seed_is_derived_from_the_key_and_the_segment` (stable, 199 distinct seeds for 199 segments, per-key, the payload seeds, a rerun reproduces, no seed without a key, a pinned hash so a change of derivation fails), `test_the_runner_keys_seeds_to_the_session` (session id by default, `ltxv_seed_key` wins, no `ltxv_seed` field, an old config still loads), `test_reference_is_fitted_to_the_render_frame` (cover within 5 %, pad + warning, strict refuses, small and uneven-border warnings), `test_reference_is_checked_before_the_run_renders` (the timing record, strict stops the run, an unreachable server defers), `test_story_load_checks_the_reference` (missing, undecodable, empty pose refused; no image needs no pose) |
| Full suite | `python -m tests.run` | **69 passed, 0 failed** (baseline 64) |
| pyflakes | changed files | clean apart from the pre-existing `runner.py` `nonlocal state` and `test_i2v.py` `json` findings (ledger, BATCH_006) |
| LTX-09 row validation ("X-05 reruns reproduce") | — | **not run**: needs the GPU session; the derivation it relies on is pinned offline |
| RUN-11 row validation ("loader tests") | as above | pass; X-12 remains the gate for replacing the Lego asset |

### Deviations

None of substance. The border-uniformity threshold (sd 12 on 0-255) and the "smaller than the subject area" rule for letterboxed images are this batch's choices; the record names the checks, not the numbers.

### Rollback

`git revert` of the batch commit.

### Next batch

Phase 1: the Showrunner's clock and memory (ARCH-02, ARCH-03, ARCH-05, ARCH-16, ARCH-17) with the authority rule (ARCH-01).

### Commit message

```
Render seeds derive from the session and the segment; the reference is checked before a run

LTX-09: every render request carries seed = hash(key, segment), the key
being the session id unless ltxv_seed_key names an earlier run, so a
rerun reproduces the noise and experiment arms share it; the fixed
ltxv_seed goes. RUN-11: the story's seed image must exist and decode
and the protagonist's reference_pose must say what it shows (at load);
at run start the client fits it to the server's render size: a master
within 5 % of the aspect is cover-resized, anything else is letterboxed
with a warning or refused under reference_fit: strict, and the verdict
goes to the timing log.
```
