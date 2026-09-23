# BATCH_018 — Preparation for X-05 and X-06: the caption-prose compiler arm and the candidate blueprint forms (LTX-01, LTX-06, LTX-02 prep)

**Status:** COMPLETED as preparation (the rows stay EXPERIMENT_REQUIRED; X-05 and X-06 need the GPU) · **Date:** 2026-09-23 · **Base:** `2d06358` (committed with BATCH_017)

## Plan

**Why.** GPU session 1 (X-03 to X-06) may be rented only when every local harness and comparison input is ready (Q8). The record permits exactly this for LTX-01: "build the comparison compiler behind an off-by-default experiment flag" once RUN-01 and RUN-13's replay harness exist (both done). LTX-06 is part of the LTX-01 arm; LTX-02's candidate forms are X-06's inputs. Nothing is enabled: `compiler` defaults to `"labels"` in the model and in every config; `stories/lego-void/story.json` is unchanged.

## Result

| File | Change |
|---|---|
| `src/story/compiler_prose.py` | New. The LTX-01 arm with the same inputs as the production compiler: style clause (full style on the opening clip, anchor after); the picture the clip opens on from the world (each character's short look, posture, position, activity, what it holds or wears; props with their state; "exactly as the shot opens"; the reference pose described on the opening clip); the framing as prose ("A medium shot frames Lego Man straight-on, and the camera remains static" / "… as the camera slowly pushes in toward him"); entering entities introduced with their look where the action names them, or in an entrance sentence (LTX-03 inherited); the action with the clip's sound woven into its first sentence (LTX-06); dialogue in the single-speaker form (LTX-05); "By the end of the shot, …"; "Apart from that, the scene is silent." on quiet clips (LTX-06); the narrator as an unseen voice in the same paragraph; a word-count guard (`word_note`: opening 150-200, continuing 80-120, ceiling 200) logged per clip. First-person beats (ARCH-19) are written as a point-of-view shot. |
| `src/models/config.py`, `src/graph/director_engine.py` | `compiler: "labels"` (default) or `"prose"`; an unknown value is refused. |
| `scripts/experiments/x06_forms.json` | LTX-02's candidate forms for the Lego story (positive-prose `visual_style`, a style clause, identity invariants) and X-06's four anchor arms (none, style, identity, both), taken from review 02 §6-7. |
| `tests/test_experiment_arms.py` | The prose compiler's paragraph, order and sentences; the engine uses it only when asked. |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | The arm and the experiments folder. |

### Validation

| Check | Result |
|---|---|
| `test_the_prose_compiler_writes_one_caption_paragraph`, `test_the_engine_uses_the_prose_arm_only_when_asked`, `test_the_arms_are_off_by_default` | pass |
| Session A through the arm: `replay_compiler.py run logs/batch-005/session-a.replay.json --compiler src.story.compiler_prose:compile_clip --out logs/batch-017/session-a.prose.json` | 64 of 64 compiled; 155.6 words mean (opening 270, continuing 154) against 122.6 for production: the Lego anchor and short look are long, which is exactly what X-06's arms shorten. X-05's comparison input is this file and the production one. |
| Full suite | 111 passed |

### Not done here

X-05 and X-06 themselves (GPU). The GPU runner for session 1 is the next preparation step.
