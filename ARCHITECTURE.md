# Architecture

## Design Philosophy

Agents don't communicate directly. They read from and write to a shared state object — a whiteboard. One agent writes, the next reads. LangGraph orchestrates execution order and merges updates.

Most agents work with plain text — prose summaries, recent story beats, the user's command. Context is context; a well-written paragraph carries the same information as a structured dict. Only the Memory agent ("Sheldon") and the Threads agent ("Chekhov") output structured data — both need stable IDs and queryable state that doesn't drift over 100+ turns. Sheldon tracks world facts (entities, inventory, locations); Chekhov tracks narrative promises (setups awaiting payoff).

Implicit qualities like mood, tone, and time of day are never tracked. The agents infer these naturally from the story history.

## Story Blueprint

The story world is defined once in `story.json` — a blueprint that establishes everything the agents need to know before the first turn. This follows the same pattern as Comic Chaos blueprints: minimal but complete.

```python
class Protagonist(BaseModel):
    name: str
    description: str

class Story(BaseModel):
    title: str
    setting: str
    protagonist: Protagonist
    narrative_premise: str
    world_constraints: list[str]
    tone_guidelines: list[str]

    @classmethod
    def from_json(cls, path: Path) -> "Story":
        data = json.loads(path.read_text())
        return cls(**data)
```

The blueprint splits its constraints into two buckets, each owned by a different agent:

- **`world_constraints`** — hard facts about the world (LEGO physics, rover capacity, oxygen limits). Given ONLY to Sherlock, who enforces them. Tolkien writes freely and may violate them; Sherlock catches it.
- **`tone_guidelines`** — stylistic direction (LEGO Movie energy, dry humor, physical comedy). Given ONLY to Wilde, who polishes Tolkien's draft for voice.

This split is deliberate: it sharpens the benchmark. In `core`, Tolkien writes freely and Sherlock enforces world constraints — but nobody polishes tone. In `full_cast`, Wilde adds tone enforcement on top.

**Solo is the exception.** The single-LLM baseline receives the *entire* blueprint — setting, protagonist, premise, world_constraints, and tone_guidelines — because it has no helper agents to decompose the work across. The question solo answers is "can one well-briefed LLM match a decomposed pipeline?", not "can an unbriefed LLM?".

The **narrative_premise** is the thematic engine — the underlying direction that guides the narrator when the user's commands are ambiguous. It goes to Tolkien and to the solo agent.

## Scenario

The scenario is separate from the story. It is a bare JSON list of user command strings — no schema, no Pydantic model. The story blueprint handles all world-building.

```json
["Wake up in my quarters...", "Try to sit up in bed", ...]
```

Loaded directly in the runner:

```python
turns = json.loads(Path("test_scenario.json").read_text())
for turn_number, user_input in enumerate(turns, start=1):
    ...
```

The single benchmark scenario is 100 turns that test inventory persistence, character tracking, and world rule consistency — all woven into one continuous playthrough.

## State (Pydantic Models)

State is intentionally lean. Most of it is text that agents read as prose context. Only the Memory agent's structured output uses Pydantic models — everything else is strings and lists.

```python
from pydantic import BaseModel, Field

class StoryBeat(BaseModel):
    turn: int
    user_input: str
    narration: str

class StoryState(BaseModel):
    """Shared state for an interactive storytelling session.

    Most fields are plain text. Agents receive prose context and
    produce prose output. Only Sheldon (Memory) and Chekhov (Threads)
    write structured data.
    """
    # Current turn
    turn_number: int = 0
    user_input: str = ""
    current_narration: str = ""

    # World state — maintained by the Memory agent (Sheldon) as structured data
    world_state: str = ""  # Sheldon's structured summary (serialized JSON string)

    # Narrative threads — maintained by Chekhov as structured data
    open_threads: str = ""  # Chekhov's thread list (serialized JSON string)

    # History
    history: list[StoryBeat] = Field(default_factory=list)
    summary: str = ""  # Compressed prose summary of older history

    # Consistency
    consistency_flags: list[str] = Field(default_factory=list)
    contradiction_count: int = 0

    # Meta
    config_name: str = ""

    # Story blueprint fields — set once at initialization
    story_setting: str = ""
    protagonist_name: str = ""
    protagonist_description: str = ""
    narrative_premise: str = ""
    world_constraints: list[str] = Field(default_factory=list)  # given only to Sherlock
    tone_guidelines: list[str] = Field(default_factory=list)    # given only to Wilde

    def get_recent_beats(self, count: int = 5) -> list[StoryBeat]:
        """Get the most recent story beats."""
        return self.history[-count:]

    @classmethod
    def initialize(cls, story: "Story", config_name: str) -> "StoryState":
        """Create initial state from a story blueprint."""
        return cls(
            summary=story.setting,
            story_setting=story.setting,
            protagonist_name=story.protagonist.name,
            protagonist_description=story.protagonist.description,
            narrative_premise=story.narrative_premise,
            world_constraints=list(story.world_constraints),
            tone_guidelines=list(story.tone_guidelines),
            config_name=config_name,
        )
```

## Structured Output Models

Two agents return structured output: Sheldon (Memory) tracks world state, and Chekhov (Threads) tracks open narrative setups. Everyone else works with plain text.

### MemoryUpdate — Sheldon

Sheldon parses the narration and extracts world state as validated JSON:

```python
class MemoryUpdate(BaseModel):
    """Structured output from the Memory agent (Sheldon)."""
    characters: dict[str, dict] = Field(default_factory=dict)
    locations: dict[str, dict] = Field(default_factory=dict)
    protagonist_location: str = ""
    inventory: list[str] = Field(default_factory=list)
    summary_update: str = ""  # only populated every N turns
```

Sheldon MERGES updates — if it doesn't mention a field, the existing value is preserved. The `world_state` field on `StoryState` stores the serialized version of Sheldon's accumulated output.

### ThreadUpdate — Chekhov

Chekhov tracks story threads — setups that have been introduced but not yet paid off (Chekhov's gun). New threads are appended; existing threads are closed when the narration explicitly references their payoff.

```python
from typing import Literal

class NarrativeThread(BaseModel):
    id: str                                    # stable slug, e.g. "gear_piece_mystery"
    description: str                           # one-line setup
    introduced_turn: int
    status: Literal["open", "closed"] = "open"
    closed_turn: int | None = None
    payoff_summary: str | None = None

class ThreadUpdate(BaseModel):
    """Structured output from the Threads agent (Chekhov)."""
    new_threads: list[NarrativeThread] = Field(default_factory=list)
    close_threads: list[str] = Field(default_factory=list)      # existing IDs to close
    payoff_summaries: dict[str, str] = Field(default_factory=dict)
```

Chekhov also MERGES updates. Threads it doesn't mention stay unchanged. Stable IDs are load-bearing — Chekhov sees the current list in its prompt and must reuse existing IDs when closing or updating, generating new IDs only for genuinely new threads. The `open_threads` field on `StoryState` stores the serialized version of Chekhov's accumulated thread list.

Both agents share the `json_sanitizer` pipeline — no separate parsing infrastructure.

## Agents

Each agent has a name inspired by a famous figure who embodies their role.

### Tolkien (Narrator)

The creative core. Reads the user's command and writes the next story beat.

**Receives (as text):** story setting, narrative premise, summary, world state, open threads, recent beats, user input

**Does NOT receive:** world constraints or tone guidelines. Tolkien writes freely. Sherlock enforces physics; Wilde polishes voice.

**Writes:** `current_narration` (1-3 paragraphs, draft in `full_cast`)

The open-threads context comes with explicit guidance: *"Feel free to reference these when the user's action naturally invites it. Do not force payoffs — slow burn is fine."* Without that framing, Tolkien tries to close every thread immediately.

### Wilde (Editor)

The stylist. Takes Tolkien's draft narration and polishes it for LEGO-Movie tone — earnest, warm, dry humor, physical-comedy beats — without changing plot or facts. A light touch, not a rewrite.

**Receives (as text):** draft narration, `tone_guidelines` from the story blueprint

**Writes:** `current_narration` (overwrites the draft with the polished version)

Only used in `full_cast`. In `core` and `solo`, Tolkien's output is shown directly.

### Sherlock (Consistency)

The quality gate. Reviews the narration against established facts and the world constraints, and flags contradictions.

**Receives (as text):** current narration, `world_constraints` from the story blueprint, summary, world state, recent beats

**Writes:** `consistency_flags`, `contradiction_count`

Does NOT rewrite narration. Only flags problems. If the config allows retries, the graph routes back to Tolkien with the flags.

### Sheldon (Memory)

Maintains the world state. One of two agents that output structured JSON (the other is Chekhov).

**Receives (as text):** current narration, current world state

**Writes:** updated `world_state` (serialized `MemoryUpdate`)

Every `summary_interval` turns, also compresses old history into `summary`.

MERGES updates — if it doesn't mention a field, preserve the existing value.

### Chekhov (Threads)

The promise keeper. Tracks open narrative threads — setups that have been introduced but not yet paid off. Sheldon tracks *facts* (who exists, what's in the inventory); Chekhov tracks *narrative weight* (what the story has set up and owes the reader).

Three kinds of memory, three failure modes, no overlap: **Sherlock guards physics, Sheldon guards facts, Chekhov guards promises.**

**Receives (as text):** current narration, current open threads, recent beats

**Writes:** updated `open_threads` (serialized `ThreadUpdate`)

Runs in parallel with Sheldon on clean narration (both depend on the same narration, neither depends on the other's output). Never runs on flagged narration — the Sherlock retry loop only goes through Tolkien → Wilde → Sherlock.

Closes a thread only when the narration explicitly references its payoff or outcome. When in doubt, leaves it open. Also populates `payoff_summary` with a one-line description of how the thread was resolved, used for benchmark telemetry.

Only used in `full_cast`.

## Prompt Templates

Agent prompts live as `.md` files in `src/prompts/`, loaded at runtime via a simple template loader:

```python
# src/util/prompt_loader.py
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=32)
def _read_file(filepath: str) -> str:
    return Path(filepath).read_text(encoding="utf-8").strip()

def load_prompt(filepath: Path, **kwargs) -> str:
    content = _read_file(str(filepath))
    return content.format(**kwargs) if kwargs else content
```

Each agent has a `system.md` and `user.md` template. Variables are substituted at call time. Agents only see the blueprint fields relevant to their job — Tolkien sees setting and premise, Wilde sees tone guidelines, Sherlock sees world constraints. Chekhov sees neither (it only inspects narration and its own thread list). Example:

```markdown
# narrator.user.md
SETTING: {story_setting}

PROTAGONIST: {protagonist_name} — {protagonist_description}

NARRATIVE PREMISE: {narrative_premise}

STORY SO FAR: {summary}

WORLD STATE:
{world_state}

OPEN THREADS (story setups awaiting payoff):
{open_threads_prose}

Feel free to reference these when the user's action naturally invites it.
Do not force payoffs — slow burn is fine.

RECENT EVENTS:
{recent_beats}

USER'S COMMAND: {user_input}

Write the next 1-3 paragraphs of the story based on the user's command.
```

Note: `{open_threads_prose}` is a rendered bulleted list of *live* threads only (closed threads are kept in state but not shown to Tolkien). The "do not force" instruction is load-bearing — without it, Tolkien tries to pay off every thread on every turn and the slow-burn dynamic collapses.

## JSON Sanitizer

LLMs will produce malformed JSON — especially local models like Gemma. The `json_sanitizer.py` module provides repair functions for parsing structured output from Sheldon (`MemoryUpdate`) and Chekhov (`ThreadUpdate`). Both agents share this pipeline.

Reference implementation is in `reference/json_sanitizer.py`. Study it but adapt for this project — remove anything not needed, keep it clean.

Parse strategy for structured responses:
1. Try direct `json.loads()`
2. If that fails, try `extract_json()`
3. If that fails, try `repair_json()`
4. If all fail, skip update for this turn and log the failure

## Interaction Logger

Every LLM call is logged to `logs/` as structured JSON. Each session/run gets its own log file. This is the primary output for benchmark evaluation — logs are reviewed post-hoc by a human or LLM, not scored by an automated pipeline.

Reference implementation is in `reference/interaction_logger.py`. Study it but adapt for this project.

The log should capture everything needed to evaluate a run after the fact:

```json
{
  "session_id": "20260409_143022",
  "config": "full_cast",
  "scenario": "test_scenario",
  "story": "LEGO Mars",
  "interactions": [
    {
      "agent": "tolkien",
      "turn": 5,
      "timestamp": "2026-04-09T14:30:45",
      "model": "google/gemma-4-31b-it",
      "parameters": {"temperature": 0.7, "max_tokens": 1024},
      "prompt": {"system": "...", "user": "..."},
      "response": {"raw": "...", "parsed": null},
      "token_usage": {"prompt": 2100, "completion": 450},
      "latency_ms": 1230
    }
  ]
}
```

## Graph Topology

### Solo (`solo_graph.py`)

One LLM does everything.

```
User Input → Single Agent → Output
```

### Core (`core_graph.py`)

Tolkien → Sherlock → Sheldon (3 agents)

```
User Input → Tolkien → Sherlock ─┬─ (clean) → Sheldon → Output
                                 └─ (flags + retries left) → Tolkien
```

### Full Cast (`full_cast_graph.py`)

Tolkien → Wilde → Sherlock → [Sheldon ∥ Chekhov] (5 agents)

```
User Input
    → Tolkien (draft narration)
    → Wilde (polish tone)
    → Sherlock ─┬─ (clean) ─┬─→ Sheldon (memory) ─┐
                │           │                     ├─→ Output
                │           └─→ Chekhov (threads) ─┘
                └─ (flags + retries left) → Tolkien
```

On a Sherlock flag, the retry loop goes back to Tolkien; Wilde then re-polishes before Sherlock checks again. Sheldon and Chekhov run in parallel on clean narration — both consume the narration, each writes to an independent state field (`world_state` and `open_threads` respectively), and neither depends on the other's output. Neither runs on flagged narration; the retry loop only cycles Tolkien → Wilde → Sherlock.

## LLM Backend

Both Gemma 4 and OpenAI GPT-4o are called through the same interface via OpenAI-compatible APIs:

```python
class LLMBackend(ABC):
    async def generate(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024) -> str: ...
```

- **Gemma 4 31B** — vLLM on `localhost:8000`, OpenAI-compatible endpoint
- **GPT-4o** — OpenAI API, `OPENAI_API_KEY` from env

## Context Management

Agents are stateless between calls. Context is reconstructed each turn as plain text:

1. **Sliding window** — last N story beats verbatim (configurable, default 5)
2. **Compressed summary** — older history condensed by Sheldon every N turns
3. **World state** — Sheldon's structured output, serialized as text for other agents to read
4. **Open threads** — Chekhov's live thread list, formatted as a bulleted prose list for Tolkien to read (live threads only — closed threads are kept in state for telemetry but are not included in Tolkien's context)
5. **Blueprint fields** — only the subset each agent needs (Tolkien: setting + premise; Wilde: tone_guidelines; Sherlock: world_constraints)

Target: ~4-8K tokens per agent call.

## Configuration

Configs are YAML files loaded into Pydantic models:

```yaml
name: "full_cast"
description: "Tolkien → Wilde → Sherlock → [Sheldon ∥ Chekhov]"
graph: "full_cast_graph"
llm_backend: "gemma"
model: "google/gemma-4-31b-it"
temperature: 0.7
max_tokens_per_agent: 1024
consistency_retries: 1
summary_interval: 10
context_window_beats: 5
```
