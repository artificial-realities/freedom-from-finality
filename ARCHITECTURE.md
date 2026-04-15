# Architecture

## Design Philosophy

Agents don't communicate directly. They read from and write to a shared state object — a whiteboard. One agent writes, the next reads. LangGraph orchestrates execution order and merges updates.

Most agents work with plain text — prose summaries, recent story beats, the user's command. Context is context; a well-written paragraph carries the same information as a structured dict. Only the Memory agent ("Sheldon") outputs structured data, because its job is specifically to maintain a queryable world state that doesn't drift over 100+ turns.

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
    rules: list[str]

    @classmethod
    def from_json(cls, path: Path) -> "Story":
        data = json.loads(path.read_text())
        return cls(**data)
```

The **rules** are constraints that must always hold — LEGO physics, world limits, tone guidelines. Every agent receives the rules as part of its context. The benchmark scenario tests whether these rules are respected throughout.

The **narrative_premise** is the thematic engine — the underlying direction that guides the narrator when the user's commands are ambiguous.

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
    produce prose output. Only the Memory agent writes structured data.
    """
    # Current turn
    turn_number: int = 0
    user_input: str = ""
    user_intent: str = ""  # Chomsky's clarified version (full_cast only)
    current_narration: str = ""

    # World state — maintained by the Memory agent as structured data
    world_state: str = ""  # Sheldon's structured summary (serialized JSON string)

    # History
    history: list[StoryBeat] = Field(default_factory=list)
    summary: str = ""  # Compressed prose summary of older history

    # Consistency
    consistency_flags: list[str] = Field(default_factory=list)
    contradiction_count: int = 0

    # Director output — silent, not shown to user, stored for future I2V
    scene_description: str = ""

    # Meta
    config_name: str = ""

    # Story blueprint fields — set once at initialization
    story_setting: str = ""
    protagonist_name: str = ""
    rules: list[str] = Field(default_factory=list)
    narrative_premise: str = ""

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
            rules=list(story.rules),
            narrative_premise=story.narrative_premise,
            config_name=config_name,
        )
```

## Memory Agent Output Model

The Memory agent ("Sheldon") is the only agent that returns structured output. It parses the narration and extracts world state as validated JSON:

```python
class MemoryUpdate(BaseModel):
    """Structured output from the Memory agent (Sheldon).

    This is the only structured response schema in the system.
    All other agents work with plain text.
    """
    characters: dict[str, dict] = Field(default_factory=dict)
    locations: dict[str, dict] = Field(default_factory=dict)
    protagonist_location: str = ""
    inventory: list[str] = Field(default_factory=list)
    summary_update: str = ""  # only populated every N turns
```

The Memory agent MERGES updates — if it doesn't mention a field, the existing value is preserved. The `world_state` field on `StoryState` stores the serialized version of Sheldon's accumulated output.

## Agents

Each agent has a name inspired by a famous figure who embodies their role.

### Chomsky (Interpreter)

The front door. Parses the user's raw command into a clean, explicit intent before Tolkien writes. Resolves vague references against the world state ("the mechanic" → whoever that is right now), clarifies pronouns, and produces a short prose intent statement.

**Receives (as text):** user input, world state, recent beats

**Writes:** `user_intent` (1-2 sentences of clarified intent)

Only used in `full_cast`. In `core` and `solo`, Tolkien reads the raw user input directly.

### Tolkien (Narrator)

The creative core. Reads the user's command and writes the next story beat.

**Receives (as text):** story setting, rules, narrative premise, summary, world state, recent beats, user input

**Writes:** `current_narration` (1-3 paragraphs)

This is the ONLY output the user sees. It must respect all rules from the story blueprint.

### Wilde (Editor)

The stylist. Takes Tolkien's draft narration and polishes it for LEGO-Movie tone — earnest, warm, dry humor, physical-comedy beats — without changing plot or facts. A light touch, not a rewrite.

**Receives (as text):** draft narration, tone guidelines

**Writes:** `current_narration` (overwrites the draft with the polished version)

Only used in `full_cast`. In `core` and `solo`, Tolkien's output is shown directly.

### Sherlock (Consistency)

The quality gate. Reviews the narration against established facts and rules, and flags contradictions.

**Receives (as text):** current narration, rules, summary, world state, recent beats

**Writes:** `consistency_flags`, `contradiction_count`

Does NOT rewrite narration. Only flags problems. If the config allows retries, the graph routes back to Tolkien with the flags.

### Spielberg (Director)

Translates the narration into a cinematic scene description. Stored in state but never shown to the user. Exists for a future video pipeline (Wan I2V via DashScope).

**Receives (as text):** current narration, world state

**Writes:** `scene_description`

Only used in `full_cast`.

### Sheldon (Memory)

Maintains the world state. The only agent that outputs structured JSON.

**Receives (as text):** current narration, current world state

**Writes:** updated `world_state` (serialized `MemoryUpdate`)

Every `summary_interval` turns, also compresses old history into `summary`.

MERGES updates — if it doesn't mention a field, preserve the existing value.

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

Each agent has a `system.md` and `user.md` template. Variables are substituted at call time. The rules from `story.json` are included in every agent's context. Example:

```markdown
# narrator.user.md
SETTING: {story_setting}

PROTAGONIST: {protagonist_name}

RULES (always respect these):
{rules}

NARRATIVE PREMISE: {narrative_premise}

STORY SO FAR: {summary}

WORLD STATE:
{world_state}

RECENT EVENTS:
{recent_beats}

USER'S COMMAND: {user_input}

Write the next 1-3 paragraphs of the story based on the user's command.
```

## JSON Sanitizer

LLMs will produce malformed JSON — especially local models like Gemma. The `json_sanitizer.py` module provides repair functions for parsing the Memory agent's structured output.

Reference implementation is in `reference/json_sanitizer.py`. Study it but adapt for this project — remove anything not needed, keep it clean.

Parse strategy for Memory agent responses:
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

Chomsky → Tolkien → Wilde → Sherlock → Sheldon → Spielberg (6 agents)

```
User Input
    → Chomsky (parse intent)
    → Tolkien (draft narration)
    → Wilde (polish tone)
    → Sherlock ─┬─ (clean) → Sheldon (memory) → Spielberg (scene desc) → Output
                └─ (flags + retries left) → Tolkien
```

On a Sherlock flag, the retry loop goes back to Tolkien; Wilde then re-polishes before Sherlock checks again. Spielberg runs last and is silent — his output is stored in `scene_description` but never shown.

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
4. **Rules** — always included from the story blueprint

Target: ~4-8K tokens per agent call.

## Configuration

Configs are YAML files loaded into Pydantic models:

```yaml
name: "full_cast"
description: "Tolkien (Narrator) → Sherlock (Consistency) → Sheldon (Memory)"
graph: "full_cast_graph"
llm_backend: "gemma"
model: "google/gemma-4-31b-it"
temperature: 0.7
max_tokens_per_agent: 1024
consistency_retries: 1
summary_interval: 10
context_window_beats: 5
```
