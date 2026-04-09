# Benchmark

## Research Question

> Does a multi-agent architecture produce higher-quality interactive narratives than a single LLM, and if so, which agent combination yields the best quality-to-cost tradeoff?

## Experiment Matrix

### Configurations

| ID | Name | Agents | Pipeline |
|----|------|--------|----------|
| C1 | `single_llm` | 1 | Single Gemma 4 handles everything |
| C2 | `mas_2_agent` | 2 | Narrator → Memory |
| C3 | `mas_3_agent` | 3 | Narrator → Consistency → Memory |
| C4 | `mas_4_agent` | 4 | Narrator → Consistency → Director (silent) → Memory |
| C5 | `single_llm_openai` | 1 | Single GPT-4o handles everything (optional) |

C4 includes the Director agent, but its output is not shown to the user. It exists to measure the overhead and to prepare scene descriptions for a future video pipeline.

### Scenarios

5 predefined 50-turn playthroughs, each stressing different capabilities:

| Scenario | Focus |
|----------|-------|
| **Exploration** | Spatial navigation, environment discovery, object persistence |
| **Mystery** | Clue tracking, NPC interrogation, long-range deduction |
| **Survival** | Resource management, time pressure, environmental state changes |
| **Social Intrigue** | Multi-character dialogue, relationships, deception |
| **Puzzle** | Logic, object interaction, cause-and-effect chains |

### Total: 5 configs × 5 scenarios × 50 turns = 1,250 turns

## Metrics

### LLM-as-Judge (per turn, 1-5 scale)

A separate LLM scores each turn given the full story context up to that point.

**Narrative Coherence (NC)** — Does this turn logically follow? Any contradictions?

| 5 | Perfectly consistent, builds naturally |
|---|---|
| 4 | Consistent, minor awkwardness |
| 3 | Mostly consistent, one small contradiction |
| 2 | Notable contradiction or logical gap |
| 1 | Directly contradicts established facts |

**User Intent Fidelity (UIF)** — Did the system do what the user asked?

| 5 | Fully executes intent, enriches with detail |
|---|---|
| 4 | Executes intent with minor drift |
| 3 | Partially addresses intent |
| 2 | Largely ignores the command |
| 1 | Completely ignores user input |

**Story Quality (SQ)** — Is this engaging, well-written storytelling?

| 5 | Compelling, vivid, advances meaningfully |
|---|---|
| 4 | Good writing, clear progression |
| 3 | Adequate but not memorable |
| 2 | Bland or repetitive |
| 1 | Incoherent or fails to advance |

**World Consistency (WC)** — Do characters, locations, objects behave consistently?

| 5 | All world elements perfectly tracked |
|---|---|
| 4 | Consistent, very minor slips |
| 3 | Mostly consistent, one element off |
| 2 | Multiple world state errors |
| 1 | World state is incoherent |

### Automated Metrics

**Character Tracking Accuracy (CTA)** — At turns 10, 20, 30, 40, 50, prompt the system to list all known characters. Compare against ground truth.

**Contradiction Count (CC)** — Total consistency flags across the full run.

**Response Latency (RL)** — Wall-clock seconds per turn.

**Token Efficiency (TE)** — Total tokens consumed across all agent calls per turn.

## Judge Protocol

### Judge Model

Use a different model family than the one being evaluated to avoid self-preference bias.

### Judge Prompt

The judge prompt template lives in `src/prompts/judge.user.md` and follows the same template loading pattern as all agent prompts.

```
You are evaluating interactive fiction. You receive the story so far, the user's
latest command, and the system's response.

Rate the response on four dimensions (1-5 each). Respond ONLY with valid JSON.

STORY SO FAR:
{story_context}

USER COMMAND (Turn {turn_number}):
{user_input}

SYSTEM RESPONSE:
{system_response}

{
  "narrative_coherence": <int 1-5>,
  "user_intent_fidelity": <int 1-5>,
  "story_quality": <int 1-5>,
  "world_consistency": <int 1-5>,
  "reasoning": "<brief explanation>"
}
```

### Aggregation

Per config × scenario:
- **Per-turn scores** — raw
- **Scenario mean** — average across 50 turns
- **Early game** (turns 1-15) — setup quality
- **Mid game** (turns 16-35) — sustained quality
- **Late game** (turns 36-50) — long-range coherence (where MAS should shine)
- **Config mean** — average across all 5 scenarios

### Expected Hypothesis

- Single LLM performs well early, degrades in late-game coherence
- 2-agent (+ Memory) improves world consistency
- 3-agent (+ Consistency) reduces contradictions
- 4-agent adds Director overhead but shouldn't change user-facing quality
- Tradeoff: more agents = more latency and token cost per turn

## Report Output

1. Summary table — mean scores per config
2. Scenario breakdown — per-scenario scores per config
3. Degradation curve — coherence + world consistency over turn number per config (matplotlib)
4. Cost analysis — tokens and latency per config

## Running

```bash
python main.py benchmark --config configs/mas_3_agent.yaml --scenarios scenarios/
python main.py experiment --output results/
python main.py judge --results results/mas_3_agent/ --output results/mas_3_agent/scores.json
```
