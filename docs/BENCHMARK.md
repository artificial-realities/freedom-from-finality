# Benchmark

## Research Question

> Does a four-role decomposed workflow (beat writer, shot composer, voice-over commentator, memory curator) **match a single well-briefed LLM on the early game and outperform it on the late game**, where juggling strains the monolithic prompt?

**Vocabulary note.** In Anthropic's agents-vs-workflows terminology this is a workflow, not an agent system — a fixed, deterministic pipeline with no tool use and no LLM-directed control flow. The comparison is therefore **decomposed prompting** vs **monolithic prompting**, not "agents vs non-agents." We still use "MAS" and "solo" as shorthand for the two configurations.

Both configs do the **same work** over the same growing story — each turn produces a `Beat`, `Shot`, `Commentary`, and `MemoryUpdate`. The asymmetry is in how each faces the problem.

**Monolithic prompting (solo) has coherence for free.** One LLM sees the whole story in one context window and can cross-reference any part of it in a single pass, so consistency between beat, shot, commentary, and memory comes almost automatically. Its hard job is **juggling** — emitting `Beat + Shot + Commentary + MemoryUpdate` in one structured response, all at once, while context grows.

**Decomposed prompting (the four-agent workflow) has specialization for free.** Each agent writes one thing and never has to juggle. Its hard job is **coherence** — because context is sliced by role, each agent sees only a fraction of the whole, and long-horizon consistency has to emerge from coordination (shared state, forward-passing between specialists, Spock's one-turn-delayed context brief) rather than from a holistic view.

The expectation is that solo holds up early but degrades in the late game as juggling strains: dropped setups, forgotten rules, drifting inventory, visual inconsistencies across clips, commentary that contradicts what's on screen. The decomposed workflow should degrade more gracefully because each specialist has a narrow, stable workload — **but only if the coordination machinery holds**. If Spock's brief misses something, or a detail slips across the forward pass between agents, the workflow loses the coherence solo gets for free.

## Experiment Matrix

### Configurations

| ID | Name | Agents | Pipeline |
|----|------|--------|----------|
| C1 | `solo` | 1 | Single LLM emits `Beat` + `Shot` + `Commentary` + `MemoryUpdate` in one structured response, fully briefed |
| C2 | `mas` | 4 | Tolkien → Spielberg → Attenborough → Spock |

The spread (1 vs 4) is intentional. Every intermediate agent we considered either didn't earn its keep or folded naturally into one of the four existing roles. The comparison is decomposition-vs-monolith, not "more agents are better."

**Solo receives the full blueprint** — synopsis, `visual_style`, `tone_guidelines`, full `locations`, full `characters`, `world_constraints`, `narrative_premise`, both narrative directions. This is a fair-comparison baseline, not a strawman.

### Story

All runs use the same blueprint in `data/story.json` — a single LEGO minifigure alone in an infinite white void, doing whatever comes to mind. The setting is deliberately stripped down: one character, one featureless background, no protagonist dialogue, no locations to navigate. What's left is pure test surface for long-horizon memory: props that come and go, bits that get called back, gags that should not repeat.

This trivializes visual consistency (one character, locked background) and removes location/character tracking as dimensions — which is the point. Any drift we see in the benchmark is memory drift, not scene drift.

### Scenario

One pre-registered 180-turn playthrough split into seven labelled phases that each exercise a specific failure mode. The full turn list lives in `data/test_scenario.json`; turn-level metadata (phase, props introduced, explicit callbacks, traps, probe windows) lives in `data/test_scenario_annotations.json` and is loaded by the judge so its scoring is grounded against the design — not against its own intuitions.

| Phase | Turns | Purpose |
|---|---|---|
| Setup | 1–25 | Seed core props (ball, cap, skateboard, dog, sunglasses, hammer, plant, harmonica) in a deterministic order. Both configs should look indistinguishable here. |
| Bit introduction | 26–55 | Introduce gags and additional props. First explicit callback at turn 54. |
| Callback density | 56–90 | Heavy callbacks to phase-1 / phase-2 props. Where solo's juggling load starts to bite. |
| Adversarial | 91–115 | 12 deliberate traps: fake callback (turn 91 umbrella), protagonist speech (93, 110), single-location violation (95), knee bends (96), second minifig (98), on-screen text (100), wall in the void (101), background colour change (107), facial expression change (109), scale change (112), broken consistency with an earlier rule (113). Includes a 5-turn silent run (102–106). |
| Compound callback | 116–145 | Ambiguous and multi-prop callbacks. The umbrella from the fake callback at turn 91 finally becomes real at turn 139, so the same prop probes both a memory miss and a memory hit. |
| Sleep + memory probes | 146–165 | Sleep transition followed by direct memory probes that ask about specific earlier turns ("where is the hammer?", "what was the very first thing you did?", "whose hat are you wearing?"). |
| Coda | 166–180 | Five-turn silent reverie, then a final long-range callback to turn 1. |

| Capability | What it tests | Where it should show up |
|---|---|---|
| Prop persistence | Props from phases 1–2 still exist / are referenced ~100 turns later; consumed props stay gone; phantom props are refused | Spock's `world_state` (active props), or the solo response's `memory_update.world_state_delta` |
| Callback quality | Late-game clips ground in specific earlier turns, including under ambiguous reference ("do that hat thing again") | `narrative_memory` → `context_brief` → Tolkien's `Beat` |
| Bit variety | The system does not repeat the same gag unless the user explicitly asks for a callback | `narrative_memory` of past bits |
| Authorship under silence | On silent turns the system advances purposefully rather than producing filler — checked specifically on the 5-turn silent runs at 102–106 and 166–170 | Tolkien's `Beat` when `user_input` is empty |
| Long-horizon coherence | Late-game story stays self-consistent with everything that came before — the central dimension for the research question | `narrative_memory` fidelity + `context_brief` quality |
| LEGO-anatomy compliance | Claw hands, stiff legs, no protagonist speech, fixed face, pop-off parts; adversarial turns silently complied with score 0 | Tolkien's `Beat`; Spielberg's `Shot` |
| Visual anchor stability | The minifigure and white void remain on-model across all 180 clips | Spielberg's `Shot` |
| Commentary coherence | Attenborough's voice-over lands on visible action, holds its register, doesn't recycle phrasings | `Commentary.voiceover` text across turns |

### Total: 2 configs × N runs × 180 turns

`N` defaults to **3** for the headline benchmark (2 × 3 × 180 = 1080 turns of generated output). Multiple runs is the single most important methodological lift over a single shot: LLM stochasticity dominates a one-shot comparison. With N=3 we get a meaningful within-config spread (range across runs) and three paired observations per phase, which is enough to count how many runs favor each config descriptively. N=3 is a deliberate cost trade-off against running larger N for inferential statistics — see *Aggregation and reporting* below for why we report descriptively rather than testing for significance.

No video or audio is rendered during benchmark runs. Spielberg's `Shot.i2v_prompt` and Attenborough's `Commentary.voiceover` are logged as text; both configs honour the same Pacing gates so the commentary stream is comparable. This keeps the benchmark reproducible and cheap.

## Evaluation

Evaluation is **automated and two-layered**. Each run is scored along two orthogonal layers, both written back to Langfuse as `Score` objects keyed to the relevant `trace_id` so dashboards and CSV exports separate them cleanly.

### Layer 1 — Per-turn local rubric (every turn, LLM judge)

For each turn, the judge scores three local-quality dimensions on an integer 0–3 scale (3 = excellent, 0 = failure):

- `local.rule_compliance` — one broad dimension covering LEGO anatomy, no protagonist speech, no on-screen text, single character, the locked white void, and visual anchor on the minifig. Silent compliance with an adversarial trap scores 0; a clean refusal/redirect/improvise scores 3.
- `local.commentary_on_screen` — Attenborough's voiceover lands on visible action; empty voiceover is a first-class 3 when appropriate.
- `local.internal_coherence` — the four artifacts mutually agree this turn (shot describes what beat says, memory delta reflects the beat outcome, context_brief makes sense for the next turn).

The judge sees the turn's normalised `{beat, shot, commentary, memory}` payload (same shape for both configs), the turn's user input, and a small annotation block surfacing the phase, any prop introduced this turn, any explicit callback target, and any trap kind. It does *not* see prior turns at this layer — local quality is judged locally.

### Layer 2 — Per-window memory rubric (15 probes, LLM judge)

The 15 probe turns defined in `data/test_scenario_annotations.json` are the measurement points for long-horizon memory. At each probe the judge sees a compact transcript of the window from `window_start` to `score_at_turn` and scores three memory dimensions on the 0–3 scale:

- `window.prop_persistence`
- `window.callback_quality`
- `window.long_horizon_coherence`

Probes target specific failure modes: the harmonica recall (turn 60 ↔ turn 25), the fake umbrella (turn 91), the adversarial cluster (turn 113 looking back over 91–113), the ambiguous hat reference (turn 116), the direct hammer / first-action / current-hat probes (turns 154–157), and the final long-range callback (turn 173).

### The judge model

We deliberately use a **different model family** for the judge than the generator. Generation is GPT-4.1 (OpenAI); the judge is **Claude Sonnet 4.6** (Anthropic, model id `claude-sonnet-4-6`). This closes the self-preference loophole that would arise from same-family judging. The judge's system prompt is sent with `cache_control: ephemeral` so the rubric is reused across the 180 per-turn calls instead of being re-paid for each turn.

The judge runs post-hoc against finished Langfuse sessions:

```bash
python main.py judge <session_id>
```

…and uploads its scores to Langfuse via `client.create_score(...)`. Scores are `name`-prefixed (`local.*`, `window.*`) so dashboards and exports can filter by layer.

### Aggregation and reporting

The seven-phase structure of the scenario gives us seven natural aggregation buckets per run. For each `(config, run, phase, dimension)` tuple, we report the mean. The headline figures roll up to:

- Phase-mean tables for `local.*` and `window.*` dimensions, grouped by config, reported as mean across the N=3 runs with the run-to-run range as the spread.
- For each phase × dimension, the count of paired runs that favored each config (e.g., "MAS scored higher on `window.long_horizon_coherence` in 5 of 6 paired late-phase observations"). Plain-language directional read, no p-values.
- Total cost / latency per turn by config (read off Langfuse `total_cost` and per-trace latency).

No inferential statistics are reported. With N=3 paired runs the data are too small for the standard non-parametric tests to do any honest work — paired Wilcoxon at N=3 cannot reach p<0.05 without pooling tricks that would weaken rather than strengthen the methodological claim. The benchmark is positioned as a **descriptive comparison under a fixed cross-family rubric**, not a hypothesis test. The cross-family judge (Anthropic Sonnet judging OpenAI GPT-4.1) closes the self-preference loophole that would otherwise dominate same-family scoring; calibration against human raters is out of scope for this project and noted as a limitation.

The expectations the benchmark is built around — informal, no statistical thresholds:

- **E1 — Early-phase parity.** In the *setup* phase, MAS and solo should look indistinguishable across all dimensions. Both configs are fully briefed and the workload is light; any meaningful gap here would suggest a bug, not a methodology signal.
- **E2 — Late-phase MAS advantage on long-horizon coherence.** Across the *sleep_and_probes* and *coda* phases (6 paired observations across N=3 runs), MAS should score higher than solo on `window.long_horizon_coherence`. Reported as the count of paired observations favoring each config plus the per-phase mean gap.
- **E3 — Adversarial parity.** Mean `local.rule_compliance` over the *adversarial* phase should be roughly tied between configs — adversarial robustness is a prompt-discipline question, not a coordination one, so both fully-briefed configs should refuse traps at similar rates.

Failure of E2 — MAS not exceeding solo on the long-horizon dimension — is itself an interesting result, not a project failure. The diagnostic question becomes whether the loss comes from cross-agent communication overhead (details slipping across the forward pass) or from specialisation not buying enough under stress. Both are visible in the logs.

### Cost envelope

Approximate per-config cost for the full benchmark + judge cycle at N=3:

- **Generation** (GPT-4.1, both configs, 3 runs each, 180 turns):
  - Solo ≈ 540 LLM calls, MAS ≈ 2,160. Roughly $24 total.
- **Judging** (Claude Sonnet 4.6, both configs, 3 runs each):
  - Per-turn local rubric: 1,080 calls, ~3K input + ~250 output tokens, with prompt caching. ≈ $11.
  - Per-window rubric: 90 calls, ~30K input + ~400 output tokens. ≈ $5.
- **Total ≈ $35–50** for one full benchmark cycle. Add ~$15 to validate the pipeline end-to-end on a single paired smoke run before committing to the full N=3 cycle. Re-judging an existing run after rubric tweaks costs ~$15 (judge only — generation is cached on disk and in Langfuse).

## Running

```bash
# Headline benchmark — 3 paired runs per config against the 180-turn scenario.
python main.py benchmark --scenario data/test_scenario.json --runs 3

# Or run one config at a time.
python main.py play --config configs/mas.yaml --scenario data/test_scenario.json
python main.py play --config configs/solo.yaml --scenario data/test_scenario.json

# Score a finished session via the LLM-as-judge harness.
# Session ids match the on-disk log stems (e.g. mas_test_scenario_20260505_120000).
python main.py judge mas_test_scenario_20260505_120000

# Logs land in logs/<session>.md (narrative transcript) plus logs/judge/<session>.judge.json
# (aggregated rubric and check scores). Per-turn scores are also pushed to Langfuse.
```
