# Benchmark

## Research Question

> Does a multi-agent architecture produce higher-quality interactive narratives than a single LLM, and if so, which agent combination yields the best quality-to-cost tradeoff?

## Experiment Matrix

### Configurations

| ID | Name | Agents | Pipeline |
|----|------|--------|----------|
| C1 | `solo` | 1 | Single Gemma 4 handles everything |
| C2 | `core` | 3 | Tolkien → Sherlock → Sheldon |
| C3 | `full_cast` | 6 | Chomsky → Tolkien → Wilde → Sherlock → Sheldon → Spielberg |

The spread (1 / 3 / 6) is intentional — each step roughly doubles the pipeline depth so the benchmark can reveal whether added agents earn their seat.

### Story

All runs use the same story blueprint defined in `story.json` — a LEGO colony on Mars. The blueprint includes setting, protagonist, narrative premise, and a set of rules (LEGO physics, world constraints, tone) that must be respected throughout.

### Scenario

One predefined 100-turn playthrough called "The Audition" that tests all capabilities in a single continuous run:

| Capability | What it tests |
|------------|---------------|
| **Inventory persistence** | Items picked up, used, combined, given away, and consumed are tracked correctly |
| **Character tracking** | NPC names, descriptions, locations, relationships, and dialogue are remembered |
| **World rule consistency** | Constraints from the story blueprint (can't swim, rover fits two, oxygen limits, etc.) are respected |

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

### Aggregation Suggestions

When scoring runs, it's useful to break them into phases:
- **Early game** (turns 1-30) — setup quality, first impressions
- **Mid game** (turns 31-70) — sustained quality, accumulated state
- **Late game** (turns 71-100) — long-range coherence (where MAS should shine)

### Expected Hypothesis

- Solo performs well early but degrades in late-game coherence as context grows
- Core (+ Sherlock + Sheldon) improves world consistency and reduces contradictions by catching them before they compound
- Full Cast (+ Chomsky, Wilde, Spielberg) adds intent clarity and tone polish, but marginal gains may not justify the added latency
- Tradeoff: more agents = more latency per turn

## Running

```bash
# Benchmark all configs against the scenario
python main.py benchmark --scenario test_scenario.json

# Logs are written to logs/ — evaluate them afterwards
```
