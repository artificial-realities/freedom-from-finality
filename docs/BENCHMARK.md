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

One predefined 100-turn playthrough that deliberately exercises long-range memory. The scenario introduces props early, runs a series of bits in the middle, and calls back to earlier props and gags in the late game. Silent turns (empty `user_input`) are sprinkled throughout — the system must advance on `short_term_narrative` without stalling.

| Capability | What it tests | Where it should show up |
|---|---|---|
| Prop persistence | Props introduced in early turns still exist / are referenced later; consumed props stay gone | Spock's `world_state` (active props) |
| Callback quality | Late-game clips reference early-game props, bits, and running gags naturally | `narrative_memory` → `context_brief` → Tolkien's `Beat` |
| Bit variety | The system does not repeat the same gag unless the user explicitly asks for a callback | `narrative_memory` of past bits |
| Authorship under silence | On silent turns, the system advances purposefully rather than producing filler | Tolkien's `Beat` when `user_input` is empty |
| Long-horizon coherence | Late-game story stays self-consistent with everything that came before | `narrative_memory` fidelity + `context_brief` quality |
| LEGO-anatomy consistency | Physical constraints respected (no knee bends, no protagonist speech, parts can pop off and click back on) | Tolkien's `Beat`; Spielberg's `Shot` |
| Visual anchor stability | The character and void remain on-model across all 100 clips | Spielberg's `Shot` — spot-checked across turns |
| Commentary coherence | Attenborough's voice-over lands on visible action, stays in the tone the blueprint asks for, doesn't recycle phrasings | `Commentary.voiceover` text across turns |

### Total: 2 configs × 1 scenario × 100 turns = 200 turns

No actual video or audio is generated during benchmark runs. Spielberg's `Shot.i2v_prompt` and Attenborough's `Commentary.voiceover` are logged and evaluated as text. This keeps the benchmark reproducible and cheap.

## Evaluation

### No Automated Scoring

There is no LLM-as-judge pipeline or automated scoring. The interaction logger captures everything — every prompt, every response, every turn, every agent call with full context.

After runs complete, evaluation is post-hoc:

- **Manual review** — read the logs, assess quality.
- **LLM-assisted review** — hand the log files to an LLM with a scoring prompt and let it analyze.

This keeps the codebase simple and avoids self-preference bias from using the same model family as generator and judge.

### What to Look For

**Prop Persistence** — Items introduced early (soccer ball, hat, dog, etc.) still exist when referenced 30–70 turns later. Consumed or dismissed props do not silently return. Props that were "put on" are still on unless explicitly removed.

**Callback Quality** — When the user references an earlier prop or bit, does the system recognize it and build on it? Does the system offer its own callbacks on silent turns?

**Bit Variety** — Does the system avoid repeating gags it has already done? A second "moonwalk" request without a variation is a memory-drift signal.

**User Intent Fidelity** — Did the system do what the user asked? How did silent turns get filled — with genuine forward motion or with filler?

**LEGO-Anatomy Compliance** — Claw hands, stiff legs, pop-off parts, no protagonist speech. Any violation is a rule-compliance failure.

**Visual Anchor Stability** (from Spielberg's prompts) — The minifigure's description stays on-model across all 100 clips. The white void stays featureless (no accidental backgrounds, floors, skies).

**Commentary Coherence** — Attenborough's voice-over lands on what's visible, holds its register per `tone_guidelines`, and doesn't recycle the same observations. Commentary that contradicts `Beat.narration` or the shot is a coordination failure between agents.

**Long-Horizon Coherence** — The central test. Late-game narration should behave as if it has read the early game, not as if it woke up at turn 70. This is the dimension where MAS is most expected to outperform solo.

### Aggregation by Phase

Break each run into phases:

- **Early game** (turns 1–30) — setup quality, first impressions. Solo and MAS should look similar here.
- **Mid game** (turns 31–70) — sustained quality as state accumulates. Early divergence.
- **Late game** (turns 71–100) — long-horizon coherence. Where MAS should most clearly win.

### Expected Hypothesis

- Solo performs competently early — its holistic view keeps every element of the story consistent for free. It degrades in the late game as the growing context strains its ability to juggle all four concerns at once: dropped setups, forgotten props, inventory drift, visual descriptors that shift across clips, commentary that starts contradicting the scene.
- MAS pays a coherence cost upfront because each agent sees only a role-specific fraction of the story. If Spock's brief, the shared state, and the forward-pass communication carry enough signal, the MAS recovers that cost; if not, it loses coherence solo had for free.
- If the MAS's coordination holds, each specialist's narrow workload keeps late-game quality closer to early-game quality than solo's does — specialization starts paying off as soon as juggling begins to hurt solo.
- MAS pays a latency cost per turn (four sequential agent calls instead of one). This is the main tradeoff; the bet is that coherence gains from specialization justify the extra calls.
- If MAS does *not* beat solo on long horizons, the interesting question is whether the loss comes from cross-agent communication overhead (details slipping across the forward pass) or from specialization not buying enough under stress — both diagnosable from the logs.

## Running

```bash
# Benchmark both configs against the scenario
python main.py benchmark --scenario data/test_scenario.json

# Or run one config at a time
python main.py play --config configs/mas.yaml --scenario data/test_scenario.json
python main.py play --config configs/solo.yaml --scenario data/test_scenario.json

# Logs are written to logs/ — evaluate them afterwards.
```
