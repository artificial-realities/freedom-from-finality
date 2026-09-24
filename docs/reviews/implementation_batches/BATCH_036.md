# BATCH_036 — A faster Director model, compared headless (RUN-16, D-14)

**Status:** RESULT_READY (gpt-5.4-mini recommended; its live film runs on GPU session 4 before the live config changes) · **Date:** 2026-09-24 · **Base:** BATCH_035

## Plan and result

D-14's second lever. The row: compare a faster model than gpt-4.1 for the Director (the Showrunner keeps its model) headless on X-07/X-08's harness, for call latency (mean, p95, max), validator re-asks and the story-authority readings, and adopt only if latency falls clearly and the readings hold.

| File | Change |
|---|---|
| `src/llm/openai_backend.py`, `src/llm/__init__.py` | `reasoning_effort`: a reasoning model (the GPT-5 family) gets its effort and `max_completion_tokens`, and no temperature. These models refuse `max_tokens`, and "minimal" is refused by the 5.4+ models; "none" is the fastest. A chat model's call is unchanged. |
| `src/models/config.py`, `src/graph/director_engine.py`, `src/eval/runner.py` | `director_reasoning_effort` (""); the Director's backend is built with it; the timing log's `session_start` records it. |
| `tests/test_llm.py` | The reasoning parameters, the chat model unchanged, the engine's Director backend. |
| `scripts/experiments/director_models.py` | `screen`: the same prompts through each candidate from one world, ten calls quiet and ten with a line. `story`: the harness runs summarised per model (call times, re-asks, writing-rule words, the judge's readings). |
| `scripts/experiments/story_authority.py` | `--director-model`, `--reasoning-effort`. |

## Readings (2026-09-24 evening, priority tier, the Lego story)

**The screen** (`logs/experiments/run16/`; mean of ten calls, quiet / with a line). gpt-4.1: 2.97 / 3.62 s. gpt-4.1-mini: 2.13 / 2.58. gpt-4.1-nano: 1.83 / 1.32. gpt-4o-mini: 2.83 / 2.17. gpt-5.4-mini (no reasoning): 2.59 / 2.42. gpt-5.6-luna, -sol, -terra: 3.3-4.6. No call re-asked or failed. By reading the beats:

- gpt-4.1-nano misses requests: the paper boat drifts in and nobody becomes captain; the bounce line reaches for an off-screen prop.
- gpt-4o-mini writes the inner states the prompt forbids ("curiosity", "determination") and misreads the bounce line.

Both were dropped. gpt-5.4-nano (no priority tier) and gpt-5.5 took 7-9 s in a probe and were not screened.

**The story runs** (`logs/experiments/run16/story/`): per model, D-05's five runs of the harness (three with directions, two with silence), judged by `judge_authority.py`:

| Director | Answer calls, mean / max | Other calls, mean / p95 / max | Calls per film | Clips per beat | Back to the card during a direction | Direction held | Movement after the last line (per min) | Rule words per 100 clips (inner state / face) | Re-asks |
|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1 | 3.93 / 7.01 s | 3.40 / 5.12 / 6.23 s | 26.6 | 1.62 | 3 of 13 lines | 80 s | 0.39 | 4.7 / 0.5 | 0 |
| gpt-4.1-mini | **2.50 / 3.24** | **2.60 / 4.02** / 6.17 | 25.6 | 1.73 | 3 | 71 s | 0.24 | 3.2 / **2.3** | 1 |
| gpt-5.4-mini, no reasoning | 3.05 / 3.38 | 3.36 / 4.54 / 5.66 | **17.2** | **2.69** | **0** | **85 s** | **1.35** | **0.9 / 0** | 0 |

- **gpt-4.1-mini** is the fastest per call, but the readings do not hold. It gives up directions sooner (71 s against 80 s) and lets the silent film go stiller (0.24 moves per minute). It writes the facial expressions the Lego blueprint forbids ("smiles", "smiling": 5 times). Its clips are one short sentence (16 words), and its beats develop the viewer's idea least.
- **gpt-5.4-mini** answers lines 0.9 s sooner on average and has no long answers (max 3.4 s against 7.0 s). Its other calls cost the same per call, but it writes longer beats, so a film needs a third fewer calls, and fewer beat ends are exposed to the tail. It is the best of the three on every authority reading: it never went back to the card during a direction, held directions longest, kept the silent film moving (1.35 per minute, near D-05's pre-D-01 level of 1.13), and broke the writing rules least. Its cost is density: 33 words in two sentences per clip, against gpt-4.1's 27 words in 1.6. How that renders is untested.
- Output tokens (the screen): gpt-5.4-mini 470-500 per call against gpt-4.1's 340-420; it streams faster (≈ 190 tokens/s against ≈ 115).

**Through the JIT simulation** (each model's measured calls from its story runs resampled, `plan_early` on, eight seeds of 60 clips; `logs/experiments/run16/jit.out`):

| Director | Quiet: stalls, lowest margin | A line every 6 clips: stalls (held), lowest margin, steering p50 |
|---|---|---|
| gpt-4.1 | 0, 3.42 s | 4 (3.3 s), −1.82 s, 11.54 s |
| gpt-4.1-mini | 0, 4.41 s | 2 (1.8 s), −1.31 s, 11.54 s |
| gpt-5.4-mini | 0, 4.43 s | **0**, 0.10 s, 11.55 s |

The simulation's beats are the mock's, so gpt-5.4-mini's third fewer calls are not in it: the difference is its call-time distribution alone (no long answers).

## Disposition

Recommend **gpt-5.4-mini at `director_reasoning_effort: "none"`** for the Director. Latency falls where the film waits (answers, the tail, the number of beat ends), and the readings hold or improve. Not adopted yet: GPU session 4 runs a live film with it next to one with gpt-4.1 (same story, lines, size and cuts). That checks what the headless harness cannot: live latency and stalls, and how its denser clips render. The live config changes after that. Rollback: `director_model`.

## Validation

- `python -m tests.run`: 144 passed (1 new).

**Commit:** this batch's.
