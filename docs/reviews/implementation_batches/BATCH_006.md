# BATCH_006 — Phase-0 mechanical cleanup (CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-07, CLEAN-09, LTX-08)

**Status:** COMPLETED · **Date:** 2026-09-22 · **Base:** `5cbfe21` plus the uncommitted BATCH_005 files and the owner's uncommitted FINAL decision record

## Plan (written before any code changed)

### Why this batch

RUN-05 and RUN-13 are done, so phase 0's open rows are the cleanup ones (record §2, §5). Six of them are mechanical, have no dependencies, change no creative behaviour and share one validation boundary (`python -m tests.run`): CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-07, CLEAN-09 and LTX-08. Left for their own batches: LTX-03 + CLEAN-08 (compiler output changes, prompt-contract tests), CLEAN-04 with ARCH-04/14/18 (documentation corrections across CLAUDE.md, ARCHITECTURE.md, README, config comments and the server docstring), CLEAN-10 (a note on the untracked review files), LTX-09 (a semantic change to the render request; §5 lists it as not safe cleanup). CLEAN-05 and CLEAN-06 depend on phase-2 rows.

### Decision IDs and what each becomes

- **CLEAN-01** — `DirectorEngine._init_world` loads `session_dir/world.json` when it exists, but `run_live` and `run_play` always create a fresh timestamped session directory, so the branch never runs. Remove it; a note in the method and in `world.py`'s docstring says resume is not built. Test: an engine whose session directory already holds a `world.json` at segment 9 starts from the blueprint.
- **CLEAN-02** — Delete `data/` (`story.json`, `legoman.png`: a copy of `stories/lego-void` that differs in two lines). The tests load `stories/lego-void` (a directory is accepted by `Story.from_json`) and `stories/lego-void/legoman.png`. `Config.i2v_seed_image` defaults to `""`, meaning the story's `seed_image`; the runner resolves the image in one place (`story.seed_image or config.i2v_seed_image`) and `main.py`'s override goes, so the story is the owner and the config key is the fallback for a story without an image. The `i2v_seed_image: "data/legoman.png"` lines leave `director.live.yaml` and `director.replay.yaml`. Docs: `ARCHITECTURE.md` names `stories/<story>/story.json`; the `data/` line leaves CLAUDE.md's layout; `models/story.py`'s docstring. Test: the seed-image rule with and without a story image.
- **CLEAN-03** — Drop `Character.reference_image` (and its path resolution in `Story.from_json`) and `Entity.reference_image` (and its copy in `from_blueprint`); nothing reads either (the renderer uses the seed image). The dead key leaves `stories/lego-void/story.json` and the field lists in `ARCHITECTURE.md`. Old `world.json` files and blueprints that still carry the key load unchanged (Pydantic ignores unknown keys); a test pins that.
- **CLEAN-07** — `format_world_now` stops printing a prop's `looks:`; the compiler reads `look` from the model, the Director does not need it. Test: the line is gone; the status clipping stays.
- **CLEAN-09** — A test for the location resolver's prefix collision (two locations whose ids share a prefix: exact ids, names and prefixed forms resolve correctly; a decorated reference falls through to the substring fallback, which takes the first id contained in the text, `the_void` for "The Void Annex, far end" — the known gap, pinned as such), and a note in `ARCHITECTURE.md` that no multi-location story has run. The row's scope is tests and docs, so the fallback is not changed here; the fix (prefer the longest id) is a two-line change the owner can approve separately.
- **LTX-08** — `ltxv_negative_prompt` and its comment ("removes the green-face artifact") leave `configs/director.live.yaml`: the distilled recipe runs at `guidance_scale 1.0` and the negative prompt is never encoded (02 §2.5, server verified). The field stays in `Config` (default `""`) for the `full` variant, with a comment saying so; the same note in `ltxv.py` and `ARCHITECTURE.md`'s config listing. The server logs at startup, on `distilled`, that a request's `negative_prompt` has no effect, and per turn when a request carries one; the value passed to the pipeline is unchanged (it has no effect at guidance 1.0, and the server cannot be run here). Test: the live config loads with the default and the client omits the field from the request.

### Files likely affected

- `src/graph/director_engine.py`, `src/story/world.py`, `src/models/story.py`, `src/models/config.py`, `src/story/context.py`, `src/eval/runner.py`, `src/i2v/ltxv.py` (a comment), `main.py`, `server/ltx2_server.py` (log lines).
- `configs/director.live.yaml`, `configs/director.replay.yaml`, `stories/lego-void/story.json` (one dead key).
- `data/` (deleted), `tests/test_story.py`, `tests/test_engine.py`, `tests/test_web.py`, `tests/test_live_timing.py`, `tests/test_i2v.py`.
- `CLAUDE.md` (layout line), `docs/ARCHITECTURE.md`.

### Migrations and compatibility

- Old `world.json` files and blueprints with `reference_image` still load. Old `configs/*.yaml` with `ltxv_negative_prompt` or `i2v_seed_image` still load (the fields stay).
- The seed image's precedence today is the story's when it has one, otherwise the config's (`main.py`); the runner keeps the same rule, so no run changes its reference image.
- A story with no `seed_image` and a config with no `i2v_seed_image` gets `""`, which the render path already treats as a missing seed (it warns and skips the render); today that case cannot arise for `stories/lego-void`.

### Risks and rollback

- Low. The server's log lines are untested on hardware; they are logging only. Rollback: `git checkout` of the listed files and `git checkout -- data/`.
- CLEAN-09's characterisation assertion pins current behaviour; if the resolver is later fixed the assertion flips, which is the intended reading.

### Validation required

- `python -m tests.run` green (baseline 57 passed).
- pyflakes on every changed Python file; `py_compile` on `server/ltx2_server.py`.
- `Config.from_yaml` on the three configs.
- A mock-backend run through `main.py` with the replay config and the web player (exercises the story's seed image through the runner without a GPU).
- The uncommitted BATCH_005 tests (`test_replay`) still pass: the probe reads the live config's negative prompt with a default.

### Baseline captured

- `python -m tests.run` at batch start: **57 passed, 0 failed** (`logs/batch-006/tests-baseline.out`, 27.2 s).
- `data/story.json` versus `stories/lego-void/story.json`: two lines differ (`reference_image` path, `seed_image` present only in `stories/`); `data/legoman.png` and `stories/lego-void/legoman.png` are the same size (63967 bytes).
- References to `data/` outside logs and reviews: CLAUDE.md layout, `src/models/config.py` default, `src/models/story.py` docstring, two configs, ten test lines in five files, two `ARCHITECTURE.md` lines.
- `reference_image` on the blueprint model is read only by `Story.from_json` (path resolution) and `WorldModel.from_blueprint` (copied into the entity); no consumer.

---

## Result

### Purpose and decision IDs

The six mechanical phase-0 cleanup rows: CLEAN-01 (unreachable resume branch), CLEAN-02 (legacy `data/`), CLEAN-03 (dead `reference_image` fields), CLEAN-07 (prop looks out of "World now"), CLEAN-09 (location resolver documented as unexercised), LTX-08 (negative prompt out of the live config). No creative behaviour changes; the Director's "World now" section loses one clause per prop, which review 01 §6.4 found risk-free.

### Files changed

| File | Change |
|---|---|
| `src/graph/director_engine.py` | `_init_world` no longer loads `session_dir/world.json`; a docstring says resume is not built (CLEAN-01). |
| `src/story/world.py` | Docstring: inspected, not resumed (CLEAN-01). `Entity.reference_image` and its copy in `from_blueprint` gone (CLEAN-03). |
| `src/models/story.py` | Docstring names the story folder (CLEAN-02). `Character.reference_image` and its path resolution gone (CLEAN-03). |
| `src/models/config.py` | `i2v_seed_image` defaults to `""` (the story's image) with a comment (CLEAN-02). The negative-prompt comment says only the `full` variant encodes it (LTX-08). |
| `src/eval/runner.py` | `seed_image_for(config, story)`: the story's `seed_image`, else the config's; used by `run_play` and `run_live` (CLEAN-02). |
| `main.py` | No longer copies the story's image into the config (the runner resolves it) (CLEAN-02). |
| `src/story/context.py` | `format_world_now` prints no `looks:` for props (CLEAN-07). |
| `src/i2v/ltxv.py` | Comment on `negative_prompt` (LTX-08). |
| `server/ltx2_server.py` | On `distilled`: one startup line saying negative prompts are not encoded, one per turn when a request carries one. The value passed to the pipeline is unchanged (LTX-08). |
| `configs/director.live.yaml` | `i2v_seed_image` line gone (CLEAN-02); `ltxv_negative_prompt` and its "green-face" comment gone (LTX-08). |
| `configs/director.replay.yaml` | `i2v_seed_image` line gone (CLEAN-02). |
| `stories/lego-void/story.json` | The dead `reference_image` key gone; `seed_image` is the image (CLEAN-03). |
| `data/` | Deleted (`git rm`): `story.json`, `legoman.png` (CLEAN-02). |
| `tests/test_story.py`, `tests/test_engine.py`, `tests/test_web.py`, `tests/test_live_timing.py`, `tests/test_i2v.py` | Load `stories/lego-void` and its image (CLEAN-02). New: `test_blueprint_has_no_reference_image_field`, `test_location_resolver_forms_and_prefix_collision`, `test_engine_starts_a_fresh_world_even_if_the_session_dir_has_one`, `test_seed_image_comes_from_the_story`, `test_live_loop_renders_from_the_story_image`; the context test asserts no `looks:`; the persistence test loads a `world.json` that still carries `reference_image`; the client test asserts no `negative_prompt` in the request. |
| `CLAUDE.md` | The `data/` layout line gone. |
| `docs/ARCHITECTURE.md` | `stories/<story>/story.json` in the tiers table and the config section; `reference_image` out of the field lists; the negative-prompt comment; a paragraph under "Not built yet, by design" on resume and on the location resolver (CLEAN-01, CLEAN-09). |

### Behaviour and contract

- **Seed image.** One rule, in the runner: the story's `seed_image` when it has one, else `Config.i2v_seed_image`. Today that is exactly what `main.py` did; the difference is that any caller of `run_live`/`run_play` now gets it, not only `main.py`. The lego-void story sets `seed_image`, so no run changes its reference.
- **"World now"** loses `· looks: …` on prop lines. Status, holder and location stay.
- **Negative prompt.** The live config sends none; the client omits the field; the server's default (diffusers') is passed as before, which at `guidance_scale 1.0` is not encoded. A `full`-variant run that wants one sets `ltxv_negative_prompt` in its config as before.
- **Resume.** The engine always starts from the blueprint. Nothing could reach the old branch (every run creates a fresh timestamped session directory).
- **Location resolver.** Unchanged. The test pins the forms that work and the substring fallback's prefix collision as the known gap.

### Migrations and compatibility

- Old `world.json` files and blueprints with `reference_image` load (unknown keys are ignored; tested).
- Old configs with `i2v_seed_image` or `ltxv_negative_prompt` load (the fields stay).
- `scripts/latency_probe.py` reads the live config's negative prompt with a default of `""`, so it sends none now (its fake-server test passes).

### Validation

| Check | Command | Result |
|---|---|---|
| Targeted modules | `python -m tests.run test_story test_engine test_i2v test_web test_live_timing` (`logs/batch-006/tests-targeted.out`) | 28 passed, 0 failed (before the fifth new test was added) |
| Full suite | `python -m tests.run` (`logs/batch-006/tests-final.out`) | **62 passed, 0 failed** (baseline 57); 27.5 s wall; the uncommitted BATCH_005 tests included |
| Static check | pyflakes (scratch venv) on the 14 changed Python files (`logs/batch-006/pyflakes.txt`) | Clean apart from two pre-existing findings that predate the batch: `runner.py` `_plan`'s unused `nonlocal state` (noted since BATCH_001) and `tests/test_i2v.py`'s unused `import json` at line 50 (present at HEAD); neither is covered by a row and both are left as is |
| Server module | `python -m py_compile server/ltx2_server.py` | ok (the server itself cannot run here: no GPU, no diffusers) |
| Config load | `Config.from_yaml` on the three configs (in `test_seed_image_comes_from_the_story`) | all load; `i2v_seed_image == ""` and `ltxv_negative_prompt == ""` in each |
| Blueprint | `json.loads` on `stories/lego-void/story.json`; `test_world_from_blueprint` | valid; the world is unchanged (`lego_man`, `the_white_void`, `reference_pose` carried) |
| Entry point | `python main.py play --config <replay config on the mock backend> --player web --port 0 --segments 3 --log-dir logs/batch-006/main-replay` (`logs/batch-006/main-replay.out`) | exit 0; three clips replayed; `timing.jsonl` (24 records), `world.json`, `events.jsonl` written; the seed image reaches the render backend through the loop (pinned separately by `test_live_loop_renders_from_the_story_image`) |
| Row validations | CLEAN-01 "tests", CLEAN-02 "`python -m tests.run`", CLEAN-03 "tests", CLEAN-07 "context test", CLEAN-09 "the test", LTX-08 "none" | pass |
| Server log lines on hardware | — | **not run** (no GPU authorised in this batch; $0.00). Logging only. |

Benchmarks: none run (no GPU).

### Deviations from the plan

- One test more than planned: `test_live_loop_renders_from_the_story_image` runs the live loop with the replay backend patched to record the image path, because the `main.py` smoke run cannot show the image (the replay backend ignores it and the timing log does not record it).
- CLEAN-09's note went under "Not built yet, by design" in `ARCHITECTURE.md` together with the resume note (CLEAN-01), rather than into the testing paragraph.

### Known risks

- Low. A story without `seed_image` and a config without `i2v_seed_image` renders nothing (the render path warns "missing seed" and skips), as before the batch when the config's default file did not exist.
- The two server log lines have not run on a GPU box.

### Rollback

`git checkout HEAD -- src/graph/director_engine.py src/story/world.py src/models/story.py src/models/config.py src/story/context.py src/eval/runner.py main.py src/i2v/ltxv.py server/ltx2_server.py configs/director.live.yaml configs/director.replay.yaml stories/lego-void/story.json tests/test_story.py tests/test_engine.py tests/test_web.py tests/test_live_timing.py tests/test_i2v.py data/` and revert the doc hunks (`CLAUDE.md`, `docs/ARCHITECTURE.md` also carry BATCH_005's uncommitted hunks; revert only this batch's lines).

### Unresolved blockers

None. For the owner, outside this batch's scope: the location resolver's prefix collision has a two-line fix (prefer the longest location id in the substring fallback, `src/story/validator.py` `resolve_location`), which CLEAN-09's scope (tests, docs) does not cover; the pinned assertion in `test_location_resolver_forms_and_prefix_collision` flips when it lands.

### Working tree: two uncommitted batches

BATCH_005 was not committed (not asked for), so its files sit next to this batch's:

- BATCH_005: `scripts/replay_compiler.py` (new), `tests/test_replay.py` (new), `scripts/latency_probe.py`, `docs/reviews/implementation_batches/BATCH_005.md`, and hunks in `CLAUDE.md` (three layout lines, the tests line), `docs/ARCHITECTURE.md` (the testing paragraph's last sentence) and the ledger.
- BATCH_006: everything else listed above, `docs/reviews/implementation_batches/BATCH_006.md`, and hunks in `CLAUDE.md` (the `data/` line), `docs/ARCHITECTURE.md` (four edits and one paragraph) and the ledger.

The three shared files carried hunks from both; they were split by batch for the two commits.

### Recommended next batch

LTX-03 + CLEAN-08: the compiler fixes (entering and returning entities described in the action; "the camera {motion}" grammar), with compiler tests and a `replay_compiler.py run --verify` against session A to show exactly which prompts change and how. After that, the documentation batch (CLEAN-04, ARCH-04, ARCH-14, ARCH-18, CLEAN-10), then LTX-09 (derived seeds) on its own, then phase 1 opens with ARCH-06.

### Suggested commit message

```
Phase-0 cleanup: data/ deleted, dead fields and branches out, no negative prompt

The tests load stories/lego-void and the story owns its reference image
(Config.i2v_seed_image is only the fallback for a story without one, and
main.py no longer copies it into the config). Character.reference_image
and Entity.reference_image, read by nothing, are gone; old blueprints and
world.json files still load. The engine's unreachable resume branch is
removed and documented as not built. "World now" no longer prints a
prop's look. The location resolver's prefix collision is pinned by a
test and noted as unexercised. ltxv_negative_prompt leaves the live
config because the distilled recipe never encodes it, and the server
says so (CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-07, CLEAN-09, LTX-08).
```

Commit: created on the owner's instruction as the commit after `59f5243` (BATCH_005) on main; its hash is recorded in the ledger by the next session.
