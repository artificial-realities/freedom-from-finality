# Benchmark

## Research Question

> Does decomposing interactive storytelling into specialized agents (beat writer, shot composer, memory curator) produce more coherent long-horizon narratives than a single well-briefed LLM handling all tasks?

A single LLM *can* emit a beat, a shot prompt, and a state update in one structured response — we test exactly that baseline. The expectation is that solo holds up early but degrades in the late game as context accumulates: dropped setups, forgotten rules, drifting inventory, visual inconsistencies across clips. The MAS should degrade more gracefully because Supervisor's `context_brief` keeps Tolkien's input roughly constant in size, regardless of run length.

## Experiment Matrix

### Configurations

| ID | Name | Agents | Pipeline |
|----|------|--------|----------|
| C1 | `solo` | 1 | Single LLM emits `Beat` + `Shot` + `MemoryUpdate` in one structured response, fully briefed |
| C2 | `mas` | 3 | Tolkien → Spielberg → Supervisor |

The spread (1 vs 3) is intentional. Every intermediate agent we considered (prose polisher, thread tracker, intent interpreter, consistency gate) either didn't earn its keep or folded naturally into one of the three existing roles. The comparison is decomposition-vs-monolith, not "more agents are better."

**Solo receives the full blueprint** — synopsis, `visual_style`, full `locations`, full `characters`, `world_constraints`, `narrative_premise`, both narrative directions. This is a fair-comparison baseline, not a strawman.

### Story

All runs use the same blueprint in `story.json` — a single LEGO minifigure alone in an infinite white void, doing whatever comes to mind. The setting is deliberately stripped down: one character, one featureless background, no dialogue, no locations to navigate. What's left is pure test surface for long-horizon memory: props that come and go, bits that get called back, gags that should not repeat.

This trivializes visual consistency (one character, locked background) and removes location/character tracking as dimensions — which is the point. Any drift we see in the benchmark is memory drift, not scene drift.

### Scenario

One predefined 100-turn playthrough that deliberately exercises long-range memory. The scenario introduces props early, runs a series of bits in the middle, and calls back to earlier props and gags in the late game. Silent turns (empty `user_input`) are sprinkled throughout — the system must advance on `short_term_narrative` without stalling.

| Capability | What it tests | Where it should show up |
|---|---|---|
| Prop persistence | Props introduced in early turns still exist / are referenced later; consumed props stay gone | Supervisor's `world_state` (active props) |
| Callback quality | Late-game clips reference early-game props, bits, and running gags naturally | `narrative_memory` → `context_brief` → Tolkien's `Beat` |
| Bit variety | The system does not repeat the same gag unless the user explicitly asks for a callback | `narrative_memory` of past bits |
| Authorship under silence | On silent turns, the system advances purposefully rather than producing filler | Tolkien's `Beat` when `user_input` is empty |
| Long-horizon coherence | Late-game story stays self-consistent with everything that came before | `narrative_memory` fidelity + `context_brief` quality |
| LEGO-anatomy consistency | Physical constraints respected (no knee bends, no speech, parts can pop off and click back on) | Tolkien's `Beat`; Spielberg's `Shot` |
| Visual anchor stability | The character and void remain on-model across all 100 clips | Spielberg's `Shot` — spot-checked across turns |

### Total: 2 configs × 1 scenario × 100 turns = 200 turns

No actual video is generated during benchmark runs. Spielberg's `Shot.i2v_prompt` is logged and evaluated as text. This keeps the benchmark reproducible and cheap.

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

**LEGO-Anatomy Compliance** — Claw hands, stiff legs, pop-off parts, no speech. Any violation is a rule-compliance failure.

**Visual Anchor Stability** (from Spielberg's prompts) — The minifigure's description stays on-model across all 100 clips. The white void stays featureless (no accidental backgrounds, floors, skies).

**Long-Horizon Coherence** — The central test. Late-game narration should behave as if it has read the early game, not as if it woke up at turn 70. This is the dimension where MAS is most expected to outperform solo.

### Aggregation by Phase

Break each run into phases:

- **Early game** (turns 1–30) — setup quality, first impressions. Solo and MAS should look similar here.
- **Mid game** (turns 31–70) — sustained quality as state accumulates. Early divergence.
- **Late game** (turns 71–100) — long-horizon coherence. Where MAS should most clearly win.

### Expected Hypothesis

- Solo performs competently early but degrades in late-game coherence as context grows — dropped setups, forgotten NPCs, inventory drift, visual descriptors that shift across clips.
- MAS keeps a roughly constant per-agent context thanks to Supervisor's `context_brief`, so late-game quality stays closer to early-game quality.
- MAS pays a latency cost per turn (three sequential agent calls instead of one), but Supervisor's shorter-input effect partially offsets it.
- If MAS does *not* beat solo on long horizons, the interesting question is whether the loss comes from cross-agent communication overhead or from Supervisor's filtering being too aggressive — both diagnosable from the logs.

## Running

```bash
# Benchmark both configs against the scenario
python main.py benchmark --scenario test_scenario.json

# Or run one config at a time
python main.py play --config configs/mas.yaml --scenario test_scenario.json
python main.py play --config configs/solo.yaml --scenario test_scenario.json

# Logs are written to logs/ — evaluate them afterwards.
```
