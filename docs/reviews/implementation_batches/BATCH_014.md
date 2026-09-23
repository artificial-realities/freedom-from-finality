# BATCH_014 — One duration authority and the warm-shape contract (RUN-01, RUN-08, CLEAN-05, CLEAN-06)

**Status:** COMPLETED (offline); the server half of RUN-08/CLEAN-05 is not run on hardware · **Date:** 2026-09-23 · **Base:** `5c3106c`

## Plan

**Why this batch.** Phase 2 is "one coordinated change" in the record: `ClipShape` and its registry (RUN-01), the client/server warm-shape contract (RUN-08), the server's neutral warm-up and per-turn log (CLEAN-05, depends on RUN-08) and the duration-field cleanup folded into RUN-01 (CLEAN-06's second half). One contract (how long a clip is and who may say so), one boundary (shape, client, runner and engine tests; the JIT simulation; the suite). RUN-03, ARCH-10 and LTX-04 build on the shape and are the next batch.

**Decision IDs.**
- **RUN-01** — An immutable `ClipShape` (fps, render_frames, overlap_frames, delivered_frames, delivered_seconds) and a startup `ShapeRegistry` from the config and the server's frame rate. Each beat's clips get the selected shape before planning; every consumer reads it; the hold comes from delivered frames; `EventRecord.duration` and `Shot.duration_seconds` become float delivered seconds; `i2v_duration` maps once and goes. Start with the six-second shape; extra shapes registered for X-18 without another derivation. Approval of the registry does not approve variable duration (§7): the production policy selects the default for every clip.
- **RUN-08** — `/health` reports `warm_frames`; the client refuses to start when a registered shape is missing; the client/registry, not `remote_setup.sh`, is the source of the frame counts.
- **CLEAN-05** — A neutral warm-up sentence; warm every configured shape (from RUN-08's registry); a per-turn log of prompt words and frame count.
- **CLEAN-06** — `Shot.duration_seconds` as the shape's float delivered seconds (the narrator half landed in BATCH_012).

**Files.** `src/i2v/shape.py` (new), `src/i2v/{base,ltxv,replay}.py`, `src/models/config.py`, `src/models/responses.py`, `src/story/{world,pacing}.py`, `src/state/story_state.py`, `src/graph/{__init__,director_engine}.py`, `src/agents/director.py`, `src/eval/runner.py`, `src/util/story_log.py`, `server/ltx2_server.py`, `server/remote_setup.sh`, `server/vast_run.sh`, `scripts/{latency_probe,replay_compiler}.py`, `configs/director.live.yaml`, tests, docs.

**Migrations.** Old configs: `i2v_duration` maps onto `clip_seconds` when that is 0 or absent, then is dropped. Old `events.jsonl` integer durations load as floats. The default clip length becomes 6 s (145 frames) everywhere, including the headless config, which used to fall back to 5 s.

**Risks / rollback.** A run now refuses to start when the server has not warmed its shapes (the intended behaviour) or when the server is unreachable at start (it used to try anyway). The player holds each clip for its shape, not its probed file length; a file of another length is flagged in the timing log (`shape_mismatch`). Rollback: revert the commit.

**Validation required.** RUN-01: every consumer reads the selected shape; an unregistered shape is refused. RUN-08: mocked single- and multi-shape `/health`. Phase-2 gate: a replay run holds each clip for its selected delivered length; unwarmed shapes refused. Suite, JIT simulation.

**Baseline.** 91 passed at `5c3106c`.

## Result

### Files changed

| File | Change |
|---|---|
| `src/i2v/shape.py` | New. `snap_to_frame_grid` (the one frame-grid function), `ClipShape` (frozen; `from_seconds`, `delivered_seconds`, `record()`), `ShapeRegistry` (`from_config`, `select` = the default, `get` refuses an unregistered count, `check_warm`, `warmup_frames`), `seconds_label`, and `python -m src.i2v.shape <config>` printing the warm-up list. |
| `src/i2v/base.py`, `ltxv.py`, `replay.py` | `synthesize(..., shape=)` replaces `duration=`; the LTX client sends `shape.render_frames` and derives nothing (`seconds_to_frames`, `self.duration` gone); `frame_rate` is a float; `check_shapes(registry)` (RUN-08: reachable server, same fps, every registered frame count in `warm_frames`). |
| `src/models/config.py`, `configs/director.live.yaml` | `clip_seconds: float = 6.0`, `extra_clip_seconds`; `i2v_duration` removed (mapped once by a validator). |
| `src/graph/director_engine.py`, `src/graph/__init__.py`, `src/agents/director.py` | The engine takes the run's registry; the shape is selected at the plan, before the Director call; emit records its delivered seconds and sets `state.current_shape`; the Director's system prompt states the default shape's length (static, cached). |
| `src/eval/runner.py` | The registry is built at start at the renderer's frame rate; `_check_shapes` refuses before anything renders (a `shapes` timing record either way); `session_start` records `shape` and `shapes`; `fire` records `frames`; the background render passes the shape and holds the item for `delivered_seconds`, checking the file's length (`shape_mismatch`); the render in flight counts its own hold in the schedule; `_get_clip_duration` gone. |
| `src/models/responses.py`, `src/story/world.py`, `src/state/story_state.py`, `src/story/pacing.py`, `src/util/story_log.py`, `scripts/replay_compiler.py` | Float delivered seconds; `current_shape`; the terminal loop's story clock falls back to the shape, never to a config number. |
| `server/ltx2_server.py` | `/health` `warm_frames`; `WARMUP_PROMPT` a story-agnostic sentence; each turn logs its frames, prompt words and "NOT WARMED" when a frame count was not warmed. |
| `server/vast_run.sh`, `server/remote_setup.sh` | `up` and `restart` pass `LTX2_WARMUP_FRAMES` from `python -m src.i2v.shape` for `AR_CONFIG` (default the live config) unless set; the setup script's default stays the live config's shape for a start without `vast_run.sh`. |
| `scripts/latency_probe.py` | Imports the frame grid from `src/i2v/shape.py`. |
| `tests/test_shape.py` | New, 7 tests (below); `test_engine`, `test_timing`, `test_web`, `test_i2v` updated to the shape. |
| `CLAUDE.md`, `docs/ARCHITECTURE.md`, `server/README.md` | The shape, the warm contract. |

### Validation

| Check | Command | Result |
|---|---|---|
| RUN-01 | `test_a_clip_shape_is_frames`, `test_the_registry_holds_the_run_s_shapes`, `test_one_duration_field`, `test_every_consumer_reads_the_selected_shape` | pass: 6 s → 145 frames, 5 → 121, 1 → 25, immutable; default + experiment shapes, `select` is the default, 121/144 refused; `i2v_duration` gone and mapped; in a replay run the Director is told "1" second clips, every request carries 25 frames, the player holds 25/24 s, the event record and the scene clock count it; a 1 s file under a 2 s shape is flagged |
| RUN-08 | `test_the_client_checks_the_server_s_warm_shapes`, `test_a_run_is_refused_when_its_shape_is_not_warm` | pass: mocked `/health` with one shape and three shapes accepted; a missing shape, no `warm_frames`, 25 fps and an unreachable server refused with the reason; the live loop refuses before any request, with the `shapes` record |
| CLEAN-05 | `test_the_server_warms_neutrally_and_reports_its_shapes` | pass on the source (the server needs a GPU to import): no story words in the warm-up sentence, `warm_frames` reported and filled, per-turn frames and words; `py_compile` clean |
| Phase-2 gate | the replay run above and `tests/test_web.py::test_web_player_serves_replayed_clips` (held for 25/24 s) | pass |
| JIT simulation | `tests/test_jit.py` | pass on the shape (the baseline's 145-frame shape) |
| Full suite | `python -m tests.run` | **98 passed, 0 failed** (91 before) |
| pyflakes | `src`, `scripts`, the new test | clean apart from the pre-existing `runner.py` finding |
| On hardware | — | **not run**: the server's `warm_frames`, its neutral warm-up and its log lines, and `vast_run.sh` passing the frame list, run first in GPU session 1 |

### Deviations

- `overlap_frames` is the continuity window (9) and `delivered_frames` equals `render_frames`: today the player shows the whole file, overlap included. LTX-12's candidate would deliver `render − overlap` through the same object; that is experiment-gated and not done.
- The server keeps its own `_snap_to_frame_grid` (it is deployed standalone and cannot import `src`); the client now always sends a count already on the grid.

### Next batch

RUN-03 (`lead_clips` → `startup_clips`, an explicit steady-state `buffer_seconds`), ARCH-10 (the beat cap's guidance in seconds from the shape), LTX-04 (the three renderer rules and `camera_motion` as a verb phrase in the Director prompt).

### Commit message

```
A clip's length is its shape in frames, and the server must have warmed it

RUN-01: src/i2v/shape.py holds the one derivation. A ClipShape (fps,
render, overlap and delivered frames) is selected for each beat before
the Director plans; the Director is told its length, the engine records
its delivered seconds (floats now), the render request carries its
frames and the player holds its delivered frames. i2v_duration is gone
(an old value maps onto clip_seconds). RUN-08: the server lists the
frame counts it warmed in /health, the client refuses to start without
every registered shape, and vast_run.sh takes the warm-up list from the
client's registry. CLEAN-05: a story-agnostic warm-up sentence and a
per-turn log of frames and prompt words. CLEAN-06: Shot.duration_seconds
is the shape's delivered seconds.
```
