# Benchmark

## Research Question

> Does a multi-agent architecture produce higher-quality interactive narratives than a single LLM, and if so, which agent combination yields the best quality-to-cost tradeoff?

## Experiment Matrix

### Configurations

| ID | Name | Agents | Pipeline |
|----|------|--------|----------|
| C1 | `solo` | 1 | Single Gemma 4 handles everything, fully briefed |
| C2 | `core` | 2 | Tolkien → Sheldon |
| C3 | `full_cast` | 4 | Tolkien → Wilde → [Sheldon ∥ Chekhov] |

The spread (1 / 2 / 4) is intentional — each step adds a structurally justified subset of agents. `core` adds structured memory (Sheldon) to the lone narrator so facts persist across 100 turns. `full_cast` adds tone polish (Wilde) up front and narrative-thread tracking (Chekhov) at the back, with Sheldon and Chekhov running in parallel on the polished narration so the added latency is one agent-call worth, not two.

Tolkien receives `world_constraints` directly from the blueprint and is instructed to respect them upfront — there is no dedicated consistency agent. The design trades a reactive quality gate for proactive rule-awareness and a simpler topology.

### Story

All runs use the same story blueprint defined in `story.json` — a LEGO colony on Mars. The blueprint includes setting, protagonist, narrative premise, and a set of rules (LEGO physics, world constraints, tone) that must be respected throughout.

### Scenario

One predefined 100-turn playthrough called "The Audition" that tests all capabilities in a single continuous run:

| Capability | What it tests | Targets |
|------------|---------------|---------|
| **Inventory persistence** | Items picked up, used, combined, given away, and consumed are tracked correctly | Sheldon |
| **Character tracking** | NPC names, descriptions, locations, relationships, and dialogue are remembered | Sheldon |
| **World rule consistency** | Constraints from the story blueprint (can't swim, rover fits two, oxygen limits, etc.) are respected | Tolkien |
| **Tone consistency** | LEGO-Movie voice holds up across turns (earnest, tactile sound-language, physical comedy) | Wilde |
| **Thread closure** | Long-range setups get paid off; how many dangle at end of run; average thread lifetime | Chekhov |
| **Atmospheric writing** | Sparse user inputs produce vivid narration rather than filler | Tolkien |

### Total: 3 configs × 1 scenario × 100 turns = 300 turns

## Evaluation

### No Automated Scoring

There is no LLM-as-judge pipeline or automated scoring system. The interaction logger captures everything — every prompt, every response, every turn, every agent call with full context.

After runs complete, evaluation is done post-hoc:
- **Manual review** — read the logs, assess quality
- **LLM-assisted review** — hand the log files to an LLM with a scoring prompt and let it analyze

This keeps the codebase simple and avoids self-preference bias from using the same model family as both generator and judge.

### What to Look For

When evaluating runs (manually or with an LLM), these are the dimensions that matter:

**Narrative Coherence** — Does each turn logically follow from what came before? Any contradictions?

**User Intent Fidelity** — Did the system do what the user asked? Or did it drift?

**Story Quality** — Is the writing engaging? Does it have the right LEGO Movie tone? Or is it bland and repetitive?

**World Rule Compliance** — Are the rules from the story blueprint respected? Does the narrator let you swim, fit three in the rover, or ignore oxygen limits?

**Inventory Accuracy** — Are items tracked correctly? Are consumed items gone? Do combined items exist?

**Character Memory** — Are NPCs remembered? Are their descriptions, locations, and relationships consistent?

**Thread Closure** — How many introduced narrative threads were paid off vs left dangling at end of run? What's the average thread lifetime (turns between introduction and payoff)? This is the dimension where MAS is most expected to outperform solo — solo's sliding window forgets setups faster than Chekhov's persistent thread list does.

### Aggregation Suggestions

When scoring runs, it's useful to break them into phases:
- **Early game** (turns 1-30) — setup quality, first impressions
- **Mid game** (turns 31-70) — sustained quality, accumulated state
- **Late game** (turns 71-100) — long-range coherence (where MAS should shine)

### Expected Hypothesis

- Solo performs well early but degrades in late-game coherence as context grows — dropped setups, forgotten rules, inventory drift
- Core (+ Sheldon) keeps inventory and NPC memory sharp via the structured facts ledger, even as the sliding window drops old beats
- Full Cast (+ Wilde, Chekhov) adds tone polish and narrative-thread tracking. Expected biggest gains in **late game (turns 71-100)** via thread closure rate — solo and core should drop at least one long-range setup; full_cast should pay off all but the deliberately dangling one
- Tradeoff: more agents = more latency per turn, but Sheldon and Chekhov run in parallel so the full_cast penalty is smaller than the agent count suggests

## Running

```bash
# Benchmark all configs against the scenario
python main.py benchmark --scenario test_scenario.json

# Logs are written to logs/ — evaluate them afterwards
```
