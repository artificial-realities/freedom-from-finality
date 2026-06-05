# Plan 00 — Cleanup baseline

> **Status:** ready to execute (revised 2026-06-05). Self-contained. Hand to a fresh Claude session.
>
> **Sibling plans:** none yet. This is the foundation every later plan assumes.
>
> **Estimated work:** half-day, mostly mechanical after the findings extraction.

---

## 0. Revisions (2026-06-05)

Three decisions made when reviewing this plan, before first execution. **Where this section conflicts with the body below, this section wins** — the body is annotated to match.

1. **Lean findings extraction.** Only extract finding docs that mine *soon-to-be-deleted* sources: `judge-methodology.md`, `benchmark-results.md`, `four-role-architecture.md`. **Skip** `bottlenecks.md`, `load-bearing-decisions.md`, `positioning.md` for now — their source (`docs/EVOLUTION.md` §A.3/§A.4/§1.x) is being kept, so copying them now just creates a sync burden. They get spun out later, when a plan actually consumes/deletes those EVOLUTION sections. (§3.2, §3.3, §3.5 below are **deferred**, not executed.)

2. **Keep the reusable scorer.** The judge harness is reusable A/B-testing infrastructure for future evolution steps (EVOLUTION flags it as an underutilized strength). **Keep:** `src/eval/judge.py`, `src/eval/langfuse_fetch.py`, `src/llm/anthropic_backend.py`, `src/prompts/judge.*.md`, and the `judge` CLI subcommand. **Still delete:** the solo baseline, `src/eval/analysis.py` (hardcoded school session IDs), `report/`, `notebooks/`, `docs/BENCHMARK.md`. (Amends §5.4, §5.5, §6.1.) Annotations-fixture coupling is resolved in §5.6.

3. **Execute in three reviewable chunks**, each its own commit, reviewed before the next: **(a)** extract findings → **(b)** delete dead scaffolding → **(c)** rewrite framing docs.

Also: the real repo root is `/home/sbfroy/projects/ArtificialRealities` (the `Artificial Realities-RnD` references below are stale; commands use relative paths so it doesn't matter).

---

## 1. Goal

Strip the project of its IKT469 academic / benchmark identity so the codebase becomes a clean starting point for evolving Artificial Realities toward a next-generation interactive AI stack — **without losing the empirical knowledge the school project produced**.

The school project ran a pre-registered 180-turn scenario comparing a four-role decomposed workflow (Tolkien/Spielberg/Attenborough/Spock) against a monolithic single-LLM baseline, judged by Claude Sonnet 4.6 against a custom rubric. That methodology and its results are valuable — they validate the four-role split as a research pattern and document concrete bottlenecks worth fixing. But the benchmark infrastructure itself (solo agent, judge harness, scenario fixtures, LaTeX report) is dead weight for the next phase and must go.

This plan does three things, in order:

1. **Mine the school-project artifacts** (`report/*.tex`, `docs/BENCHMARK.md`, `docs/EVOLUTION.md`, `docs/ARCHITECTURE.md`) for empirical findings and architectural reasoning, and write those into a new `docs/findings/` folder. Findings must be **detailed enough** that a future plan (e.g., redesigning the four roles into a Director/Cinematographer/Editor split) can consult them and know what worked, what didn't, and what to transfer.
2. **Delete** all benchmark infrastructure, school framing, and obsolete experimentation.
3. **Rewrite** the surviving framing docs (`README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `main.py`) so they describe the project as it now is — a four-role interactive-storytelling workflow with a runtime, not a benchmark study.

After this plan executes cleanly:

- `python main.py play --config configs/mas.yaml` still works (headless dev).
- `python main.py play --config configs/mas.live-text.yaml` still works (Tk popup demo).
- `python main.py play --config configs/mas.live.yaml` still works (full live with i2v + TTS, given API keys).
- `docs/findings/` holds the preserved knowledge; `docs/EVOLUTION.md` survives untouched (it's the source-of-truth quarry for later plans); `docs/ARCHITECTURE.md` and `docs/AGENT_FLOW.md` survive in slimmer form.
- `plans/` exists with this file and a short `README.md` describing the workflow.
- No `solo`, no `judge`, no `benchmark` references remain in the source tree.

---

## 2. Preconditions

- Working tree clean (`git status`). Create a branch like `cleanup/baseline` before starting.
- Repo root: `/home/sbfroy/projects/Artificial Realities-RnD`.
- Read this entire plan once before executing any phase. The phases are ordered to avoid orphan imports — do not reorder.

---

## 3. Phase 1 — Extract findings into `docs/findings/`

**Do this first**, before any deletion. Every finding doc must cite its source files inline (e.g., `(from report/07_results.tex)` or `(EVOLUTION.md §A.3 B3)`) so the provenance is auditable.

Create the directory: `docs/findings/`.

### 3.1 `docs/findings/four-role-architecture.md`

**Audience:** any future plan that proposes redesigning, splitting, merging, or replacing one of the four roles (notably a Director/Cinematographer/Editor reframe along FilmAgent / ViMax / MovieAgent / Camera Artist lines).

**Must contain:**

- The role split that was used: Tolkien (beat writer), Spielberg (shot composer), Attenborough (voice-over commentator), Spock (memory + context curator). One paragraph per role: what it owned, what it read, what it wrote. Source: `docs/ARCHITECTURE.md` §Agents, `docs/AGENT_FLOW.md` §3.
- How the four roles coordinated: the one-turn-delayed feedback loop (Spock's `context_brief` from turn N is read by Tolkien at N+1), end-frame chaining for visual continuity, role-disciplined slicing of blueprint reads. Source: `docs/ARCHITECTURE.md` §One-Turn-Delayed Feedback Loop, §Context Management.
- The "research consensus" framing: the Director/Screenwriter/Cinematographer/Editor decomposition used by Camera Artist, FilmAgent, ViMax, MovieAgent — and how the Artificial Realities split maps to it (Tolkien≈Screenwriter+Director, Spielberg≈Cinematographer, Attenborough≈narration, Spock≈script supervisor / continuity). Source: `docs/EVOLUTION.md` §1.1, §C.7.
- What the benchmark actually showed about whether the split paid off: where MAS outperformed solo (long-horizon coherence), where it didn't (some local commentary measures), and the leading hypotheses for why. Source: `report/07_results.tex`, `report/08_discussion.tex`, `docs/EVOLUTION.md` §A.3.
- Concrete transfer notes for a future redesign: which inter-role contracts are load-bearing (one-turn delay, narration as the shared prose source between Spielberg and Attenborough), which are incidental (the specific role names, the strict serial graph topology), and which boundaries you'd want to keep no matter how the roles are re-cut (a clear separation between "what happens" and "how it's shot", and between "current truth" and "rolling memory"). Source: `docs/EVOLUTION.md` §A.4 load-bearing vs swappable.

**Target length:** 250–400 lines. This is the deepest finding doc; do not summarize away the detail that makes it useful for a redesign.

### 3.2 `docs/findings/bottlenecks.md`  — **DEFERRED (§0.1), do not create**

**Audience:** every feature plan that addresses one of the bottlenecks identified in the codebase audit. Future plans will cite specific bottleneck IDs from this doc.

**Must contain:** the eight bottlenecks B1–B8 from `docs/EVOLUTION.md` §A.3, preserved verbatim or near-verbatim, with their numeric IDs intact:

- B1 — i2v latency (~30s per 5s clip)
- B2 — four serial LLM calls per MAS turn
- B3 — Spock's full-replacement `narrative_memory` every turn
- B4 — 5-entry history window too small for late game
- B5 — JSON-mode fragility (no native structured outputs)
- B6 — Spielberg's `duration_seconds` ignored
- B7 — sequential producer in `run_live` (no overlapped i2v)
- B8 — cost (~$24 generation, ~$16 judging per benchmark cycle)

For each: the bottleneck name, where the evidence lives in code (file:line, per EVOLUTION), why it bites, and what relaxing it unlocks. Preserve the latency-budget table from `docs/EVOLUTION.md` §A.2 in this doc.

**Target length:** 150–250 lines.

### 3.3 `docs/findings/load-bearing-decisions.md`  — **DEFERRED (§0.1), do not create**

**Audience:** any plan that touches the agent loop or considers changing one of these.

**Must contain:** the "load-bearing" list from `docs/EVOLUTION.md` §A.4, each item explained in 2–4 sentences with the original rationale:

- The four-role split itself (research thesis).
- The one-turn-delayed feedback loop.
- End-frame chaining for i2v continuity.
- Structured Pydantic outputs (and why `json_sanitizer` was needed before native structured outputs).
- Async agents as functions, not classes.
- `StoryState` as the single shared object with role-disciplined slicing.

Also include the **paired "swappable / incidental" list** from the same section so a future plan can tell at a glance what's free to change (LangGraph, GPT-4.1, Wan 2.2, ElevenLabs, the white-void story, the 5-turn window, the 3-stage JSON repair, etc.).

**Target length:** 120–200 lines.

### 3.4 `docs/findings/judge-methodology.md`

**Audience:** any future plan that wants to do offline evaluation (e.g., A/B testing a new memory layer, scoring a change to the four-role split).

**Must contain:**

- The cross-family-judge principle: generator was GPT-4.1, judge was Claude Sonnet 4.6, the explicit reason being to close the self-preference loophole. Source: `docs/BENCHMARK.md` §The judge model.
- The two-layer rubric structure: per-turn local (`local.rule_compliance`, `local.commentary_on_screen`, `local.internal_coherence`) and per-window memory (`window.prop_persistence`, `window.callback_quality`, `window.long_horizon_coherence`), each on an integer 0–3 scale. Source: `docs/BENCHMARK.md` §Layer 1, §Layer 2; `src/prompts/judge.*.md` (read before deleting).
- The pre-registered scenario design: 7 phases, 180 turns, annotations file with phase / props / callbacks / traps / probe windows. Source: `docs/BENCHMARK.md` §Scenario, `report/03_scenario.tex`.
- The credibility moves: `cache_control: ephemeral` on the judge system prompt (rubric reused across 180 turns), paired N=3 runs, **descriptive rather than inferential** reporting (paired Wilcoxon at N=3 cannot reach p<0.05; the project explicitly does not claim significance). Source: `docs/BENCHMARK.md` §Aggregation and reporting.
- Cost envelope at N=3: ~$24 generation, ~$11 local rubric, ~$5 window rubric, ~$35–50 total per cycle. Source: `docs/BENCHMARK.md` §Cost envelope.

**Target length:** 100–180 lines.

### 3.5 `docs/findings/positioning.md`  — **DEFERRED (§0.1), do not create**

**Audience:** the eventual wedge-decision plan (broadcast-first vs family-bedtime vs paradigm bet) and any pitch / framing work.

**Must contain:**

- The "watch + steer" gap analysis: text RPGs (Voyage, Hidden Door, Friends & Fables) own read+steer; Showrunner / Sora own create-then-share; Neuro-sama owns lean-back AI broadcast; Genie 3 / Lucy / Oasis own play-in-a-world for ~60 seconds. Nobody owns watch+steer continuous AI video, and that gap matches the exact shape of Artificial Realities. Source: `docs/EVOLUTION.md` §1.1, §D.6.
- The recommended wedge sequence (broadcast as 6-month wedge, family as durable product) and the paradigm-bet recommendation (LTX-2 streaming substrate on a 1-year horizon). Source: `docs/EVOLUTION.md` §1.4.
- Defensibility lens: what actually creates a moat (licensed IP + rev-share, persistent content library, owned character / IP, workflow depth, distribution that competitors can't replicate, on-device inference) vs what doesn't (wrapping the latest big model, pure novelty broadcast, "we added AI", open NSFW). Source: `docs/EVOLUTION.md` §D.5.
- "Things deliberately left out" (NSFW, pure AI-TV / Showrunner clone, building on Sora, game-engine integration). Source: `docs/EVOLUTION.md` §1.6.

**Target length:** 120–180 lines.

### 3.6 `docs/findings/benchmark-results.md`

**Audience:** anyone deciding whether to keep, redesign, or replace the four-role split.

**Must contain:** the concrete empirical record from the school project. Numbers, not summaries.

- The 3-paired-run setup, what was measured per phase, the actual phase-mean scores for both configs across all six dimensions, the per-phase paired-observation counts ("MAS scored higher on X in N of 6 paired observations"). Source: `report/07_results.tex` is the primary; `logs/judge/per_turn_scores.json` and `logs/judge/per_window_scores.json` are the raw data (read them before deletion).
- The interpretation, especially around the three expectations E1 (early-phase parity), E2 (late-phase MAS advantage on long-horizon coherence), E3 (adversarial parity) — what held and what didn't. Source: `docs/BENCHMARK.md` §Aggregation and reporting; `report/08_discussion.tex`.
- The honest caveats: N=3, descriptive only, single judge model, the limitations the report itself flagged. Source: `report/08_discussion.tex`, `report/09_conclusion.tex`.

**Target length:** 150–250 lines.

### 3.7 `docs/findings/README.md`

A short index (under 60 lines): one-line summary per finding doc **that was actually created** (per §0.1: `four-role-architecture.md`, `judge-methodology.md`, `benchmark-results.md`), plus a one-paragraph statement that these are *frozen snapshots from the school-project era* — they describe what was true at the time of the IKT469 study, not necessarily what's still true after subsequent refactors. Future plans that contradict a finding should update the finding doc, not silently outdate it.

---

## 4. Phase 2 — Set up `plans/`

The `plans/` directory already exists (it contains this file). Add:

### 4.1 `plans/README.md`

Short doc (under 80 lines) describing the workflow:

- One plan file per evolution topic, numbered (`NN-topic.md`).
- Each plan is self-contained: a fresh Claude session should be able to execute it without reading any other plan.
- Plans pair feature work with the cleanup of any school-project residue tied to that feature (e.g., the Spock-memory plan retires the parts of `docs/findings/bottlenecks.md` B3 it obsoletes).
- `docs/EVOLUTION.md` is the source-of-truth quarry. It survives until every section has been spun out into a plan, then it is deleted.
- `docs/findings/` files are mutable — when a plan changes something the findings described, the plan must update the relevant finding doc (not silently outdate it).

Do **not** create stubs for other plans. They will be authored interactively over many sessions.

---

## 5. Phase 3 — Delete

Execute in the order below. Each step is independent of the others as long as Phase 1 is complete and the previous deletions in this phase are done.

### 5.1 Delete the LaTeX report (whole folder)

```
rm -r report/
```

Justification: Phase 1 §3.6 mined the results; `report/main.tex` and chapters are now redundant artifacts. The git history retains them if needed.

### 5.2 Delete the notebooks folder (whole folder)

```
rm -r notebooks/
```

Removes: `IDEA.md`, `narratron.system.md`, `blueprint.json` (Comic Chaos artifact), `i2v_chaining_test.ipynb`, `i2v_speed_benchmark.ipynb`, `wan2.2_i2v_local_test.ipynb`, `wan_test.ipynb`, `benchmark_outputs/`. User confirmed wholesale delete — nothing preserved into `docs/findings/` from this folder.

### 5.3 Delete the logs folder

```
rm -r logs/
```

All session logs from school-era runs (benchmark/, judge/, live/, play/, smoke/, root-level `mas_demo_*.md`). The runner re-creates `logs/` at runtime; nothing in code depends on contents.

### 5.4 Trim the eval infrastructure — **KEEP the scorer (§0.2)**

The judge harness is reusable A/B-testing infrastructure for future evolution steps, so it stays. Delete **only** the genuinely dead analysis script:

```
rm src/eval/analysis.py
```

`analysis.py` references hard-coded session IDs from school runs and has no callers in `src/`.

**Keep** (do NOT delete): `src/eval/judge.py`, `src/eval/langfuse_fetch.py`, `src/llm/anthropic_backend.py`, and the four `src/prompts/judge.*.md` rubric prompts. The `judge` CLI subcommand also stays (§6.1). Note these are kept as reusable infra; they may be *dormant* after this cleanup until a future eval plan re-points the rubric at product dimensions — that's fine.

### 5.5 Delete the solo / monolithic baseline

```
rm src/agents/solo.py
rm src/graph/solo_graph.py
rm src/prompts/solo.system.md
rm src/prompts/solo.user.md
rm configs/solo.yaml
```

The solo agent existed only as the benchmark control. With benchmarking gone it has no caller. Note: `src/models/responses.py` defines `SoloResponse` — confirm it has no other use (grep across `src/`), and if not, remove the `SoloResponse` class from `responses.py`.

### 5.6 Delete scenario fixtures used only by the benchmark

```
rm data/test_scenario.json
rm data/test_scenario_annotations.json
rm data/smoke.json
```

**Coupling note (from §0.2 keep-the-scorer):** the kept `judge` subcommand defaults `--annotations` to `data/test_scenario_annotations.json`, which we're deleting. The school-era annotations (LEGO white-void probe windows / callback ground-truth) are useless for future product eval anyway, so don't keep them just to satisfy a default. Instead, in chunk (c) / §6.1, make the judge's `--annotations` a **required** argument (drop the default that points at the deleted file). The scorer stays runnable; the caller supplies fresh annotations.

`data/story.json` and `data/demo_scenario.json` are **kept**. `story.json` is the runtime default story; `demo_scenario.json` is a useful canonical scripted demo input. `data/legoman.png` is kept (referenced as `i2v_seed_image` default in `src/models/config.py:48`).

### 5.7 Delete the benchmark methodology doc

```
rm docs/BENCHMARK.md
```

Findings preserved in `docs/findings/judge-methodology.md` and `docs/findings/benchmark-results.md`.

---

## 6. Phase 4 — Rewrite surviving framing

### 6.1 `main.py`

Remove the `benchmark` subcommand (its solo control is gone). **Keep the `judge` subcommand** per §0.2. After the edit, `main.py` should:

- Keep `cmd_play` and its argparse subparser. The `--scenario` flag stays on `play` (useful for scripted demos like `data/demo_scenario.json`).
- Keep `cmd_judge` and its subparser, but make `--annotations` **required** (remove the `default=DEFAULT_ANNOTATIONS` that pointed at the now-deleted fixture, per §5.6). `DEFAULT_JUDGE_MODEL`, `DEFAULT_CONCURRENCY`, and `judge_session` imports stay.
- Drop `cmd_benchmark`, `BENCHMARK_CONFIGS`, `DEFAULT_SCENARIO`, and the `run_scenario`-for-benchmark wiring that only the benchmark command used. (`run_scenario` itself stays — `play --scenario` uses it.)
- Update the module docstring at the top accordingly (drop the `benchmark` usage line; keep `play` and `judge`).

### 6.2 `README.md` (rewrite)

The current README frames the project as "a four-role workflow … benchmarked against a single LLM baseline." Rewrite as a runtime / dev project README. Required sections:

- One-paragraph project description: Artificial Realities is a four-role multi-agent interactive storytelling workflow that turns user steering between clips into a continuous flowing video story with voice-over commentary. (Drop the "academic shorthand", "monolithic baseline", "benchmark" framing.)
- The four agents (Tolkien / Spielberg / Attenborough / Spock), one line each — same content as today, no benchmark language.
- Architecture pointer → `docs/ARCHITECTURE.md` (after its own rewrite per §6.4).
- Roadmap / direction pointer → `docs/EVOLUTION.md` + `plans/`. Frame `docs/findings/` as "context from the project's earlier phase, preserved for reference."
- A short project structure tree reflecting the new shape (no `solo`, no `report/`, no `notebooks/`, no `judge`).
- Quick start: only `play` commands (the three configs), no benchmark / judge commands. Drop the `data/banner.png` image link unless `banner.png` is being kept (it isn't critical — remove it).
- Tech stack: drop "GPT-4.1" specifics if you like, but otherwise unchanged.

Keep it short — under 100 lines.

### 6.3 `CLAUDE.md`

Strip the IKT469 / University of Agder line. Conventions (Pydantic v2, async agent functions) are still valid; keep them. After the edit, `CLAUDE.md` should describe the project as a four-role interactive-storytelling workflow under active evolution per `plans/`, not an academic project.

### 6.4 `docs/ARCHITECTURE.md`

Remove all benchmark-specific sections; keep the design-philosophy and runtime mechanics. Specifically:

- **Strip:** the "comparison we actually study is … decomposed prompting vs monolithic prompting" framing throughout. Drop the "research question is whether the decomposed workflow matches solo …" paragraph. Drop the solo-graph subsection ("### Solo (`solo_graph.py`)"). Drop the "For the benchmark we bypass the buffer entirely" paragraph (replace with "Live mode runs the producer/consumer continuously; the no-video Tk popup mode runs at LLM speed without any buffer.").
- **Keep:** Design Philosophy (rewritten to drop the vs-solo framing), One-Turn-Delayed Feedback Loop, Pipeline Buffer, Story Blueprint, State (Pydantic models), Agents (each of the four), Turn Execution Order, Graph Topology (MAS only), Prompt Templates, JSON Sanitizer, Interaction Logger, LLM Backend, Context Management (rewritten to drop the solo paragraph), Configuration.
- **Update:** `Scenario` section currently says "The single benchmark scenario is 100 turns that test inventory persistence …" — replace with a one-paragraph description of `data/demo_scenario.json` as the canonical scripted demo input, with the scenario file format being a plain JSON list of user-input strings.

### 6.5 `docs/AGENT_FLOW.md`

Already mostly clean. Two small edits:

- Drop the `### 3.5 Solo — monolithic baseline` subsection entirely.
- Drop references to `src/agents/solo.py`, `src/graph/solo_graph.py`, and the "Solo" row from any tables.

### 6.6 `data/banner.png` (optional)

The current README opens with a banner image. If kept, leave it; if dropped in §6.2, also `rm data/banner.png`. Recommended: drop it (the school-era branding is gone, banner is incidental).

---

## 7. Decisions deferred

- **`data/story.json`** (the LEGO minifig in the white void) is kept untouched in this plan. It was originally designed as a benchmark measurement device (single character, locked background → any drift visible was memory drift). It still works as a default story, and replacing it without knowing the wedge audience would be premature. A future plan should revisit it once a wedge is chosen (broadcast hosts? family bedtime? something else).
- **`docs/EVOLUTION.md`** is kept untouched. It is the source-of-truth quarry for every later plan. It will be deleted only after every section has been spun out into a plan.
- The seven drop-in upgrades in `docs/EVOLUTION.md` §1.2 are out of scope here. Each will get its own plan (`01-*.md`, `02-*.md`, …).

---

## 8. Phase 5 — Validation

After Phases 1–4 are done, run the following and confirm each passes.

### 8.1 Import sanity

```
python -c "from main import main; print('ok')"
```

No `ImportError`. If anything fails, search for the missing symbol — usually a stale import in `main.py` or `runner.py`.

### 8.2 Headless dev smoke

```
python main.py play --config configs/mas.yaml --scenario data/demo_scenario.json
```

Should run through several scripted turns and exit cleanly. Logs land in `logs/<session>.md`. Does **not** require DashScope or ElevenLabs keys (video and audio disabled in `configs/mas.yaml`).

### 8.3 Live-text popup smoke

```
python main.py play --config configs/mas.live-text.yaml
```

Tk popup appears, terminal accepts input. Type one guidance line, confirm the next turn lands in the popup. Ctrl-C to exit. Does **not** require DashScope or ElevenLabs keys.

### 8.4 Live-video smoke (only if DashScope + ElevenLabs keys present)

```
python main.py play --config configs/mas.live.yaml
```

`ffplay` window appears, first clip plays after ~30s of i2v latency, voice-over comes in. This is the most expensive check; skip if API keys aren't loaded.

### 8.5 No-orphan grep

These should all return zero lines:

```
grep -rn "solo" src/ configs/ main.py            # expect: zero
grep -rn "benchmark" src/ configs/ main.py        # expect: zero
grep -rn "test_scenario" src/ configs/ main.py    # expect: zero
grep -rn "IKT469\|University of Agder\|academic project" .   # expect: only EVOLUTION.md + docs/findings/
```

`solo`, `benchmark`, `test_scenario` should be gone from code/configs. **`judge`, `anthropic_backend`, `langfuse_fetch`, `DEFAULT_JUDGE_MODEL` will still have matches — that's expected (§0.2 keeps the scorer).** Legitimate `IKT469` / academic matches survive only in `docs/EVOLUTION.md` (the quarry) and `docs/findings/*.md` (school context preserved with provenance).

### 8.6 Git status review

```
git status
git diff --stat
```

Expect: large deletions in `report/`, `notebooks/`, `logs/`, `src/eval/`, `src/agents/solo.py`, `src/graph/solo_graph.py`, `src/prompts/judge.*.md`, `src/prompts/solo.*.md`, `configs/solo.yaml`, `src/llm/anthropic_backend.py`, `data/test_scenario*.json`, `data/smoke.json`, `docs/BENCHMARK.md`. Smaller diffs in `README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/AGENT_FLOW.md`, `main.py`, possibly `src/models/responses.py` (if `SoloResponse` is removed). New files in `docs/findings/` and `plans/README.md`.

Review the diff before committing. Commit in two parts if helpful: one commit for the findings extraction (additive), one for the deletions and rewrites.

---

## 9. Resulting tree (target)

```
Artificial Realities-RnD/
├── CLAUDE.md                 # rewritten, no IKT469
├── README.md                 # rewritten, no benchmark framing
├── configs/
│   ├── mas.live-text.yaml
│   ├── mas.live.yaml
│   └── mas.yaml
├── data/
│   ├── demo_scenario.json
│   ├── legoman.png
│   └── story.json            # deferred decision
├── docs/
│   ├── AGENT_FLOW.md         # solo paragraphs stripped
│   ├── ARCHITECTURE.md       # benchmark sections stripped
│   ├── EVOLUTION.md          # quarry, untouched
│   └── findings/            # lean set (§0.1); others spun out later
│       ├── README.md
│       ├── benchmark-results.md
│       ├── four-role-architecture.md
│       └── judge-methodology.md
├── main.py                   # play subcommand only
├── plans/
│   ├── 00-cleanup-baseline.md
│   └── README.md
├── requirements.txt
└── src/
    ├── __init__.py
    ├── agents/
    │   ├── __init__.py
    │   ├── _common.py
    │   ├── attenborough.py
    │   ├── spielberg.py
    │   ├── spock.py
    │   └── tolkien.py
    ├── eval/
    │   ├── __init__.py
    │   ├── judge.py          # kept (§0.2): reusable scorer
    │   ├── langfuse_fetch.py # kept (§0.2)
    │   └── runner.py
    ├── graph/
    │   ├── __init__.py
    │   └── mas_graph.py
    ├── i2v/                  # unchanged
    ├── llm/
    │   ├── __init__.py
    │   ├── anthropic_backend.py  # kept (§0.2): judge backend / future Claude generator
    │   ├── base.py
    │   └── openai_backend.py
    ├── models/
    │   ├── __init__.py
    │   ├── config.py
    │   ├── responses.py      # SoloResponse removed if unused elsewhere
    │   └── story.py
    ├── playback/             # unchanged
    ├── prompts/
    │   ├── attenborough.system.md
    │   ├── attenborough.user.md
    │   ├── judge.local.system.md   # kept (§0.2)
    │   ├── judge.local.user.md     # kept (§0.2)
    │   ├── judge.window.system.md  # kept (§0.2)
    │   ├── judge.window.user.md    # kept (§0.2)
    │   ├── spielberg.system.md
    │   ├── spielberg.user.md
    │   ├── spock.system.md
    │   ├── spock.user.md
    │   ├── tolkien.system.md
    │   └── tolkien.user.md
    ├── state/                # unchanged
    ├── tts/                  # unchanged
    ├── ui/                   # unchanged
    └── util/                 # unchanged
```

---

## 10. What this plan does NOT do

- Does not switch from Wan 2.2 to Wan 2.7 (separate plan).
- Does not change Spock's memory layer (separate plan).
- Does not parallelize Spielberg+Attenborough (separate plan).
- Does not adopt native structured outputs (separate plan).
- Does not pick a wedge (broadcast / family) — strategic decision deferred.
- Does not delete `docs/EVOLUTION.md` — it's the quarry.
- Does not change `data/story.json` — deferred.

If you find yourself doing any of the above while executing this plan, stop and surface it. That work belongs in a separate plan.
