# Architecture

## Design Philosophy

Agents don't communicate directly. They read from and write to a shared state object — a whiteboard. One agent writes, the next reads. LangGraph orchestrates execution order and merges updates.

Implicit qualities like mood, tone, time of day, and genre are never tracked as explicit state fields. The agents infer these naturally from the story history. Only things an LLM would genuinely lose track of over 50+ turns are stored explicitly: characters, locations, inventory, and a compressed history.

## State (Pydantic Models)

All state is defined as Pydantic `BaseModel` classes for validation, serialization, and type safety. This follows the pattern from Comic Chaos where Pydantic models are used throughout for both state management and LLM response parsing.

```python
from pydantic import BaseModel, Field

class Character(BaseModel):
    name: str
    description: str
    status: str = "alive"                    # alive, dead, unconscious, absent, etc.
    location: str = ""
    inventory: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)
    first_appeared: int = 0

class Location(BaseModel):
    name: str
    description: str
    connected_to: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    characters_present: list[str] = Field(default_factory=list)

class StoryBeat(BaseModel):
    turn: int
    user_input: str
    narration: str
    active_characters: list[str] = Field(default_factory=list)
    location: str = ""
    consistency_flags: list[str] = Field(default_factory=list)

class StoryState(BaseModel):
    """Complete state for an interactive storytelling session.

    Designed to be lean — only tracks what LLMs genuinely lose track of.
    Mood, tone, genre, time of day are inferred from history by the agents.
    """
    # Current turn
    turn_number: int = 0
    user_input: str = ""
    current_narration: str = ""

    # World state
    characters: dict[str, Character] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    protagonist_name: str = ""
    protagonist_location: str = ""
    inventory: list[str] = Field(default_factory=list)

    # History
    history: list[StoryBeat] = Field(default_factory=list)
    summary: str = "The story has just begun."

    # Consistency
    consistency_flags: list[str] = Field(default_factory=list)
    contradiction_count: int = 0

    # Director output — silent, not shown to user, stored for future I2V
    scene_description: str = ""

    # Meta
    config_name: str = ""

    def get_recent_beats(self, count: int = 5) -> list[StoryBeat]:
        """Get the most recent story beats."""
        return self.history[-count:]

    @classmethod
    def initialize_from_scenario(cls, scenario: "Scenario", config_name: str) -> "StoryState":
        """Create initial state from a scenario definition."""
        protagonist = scenario.protagonist
        return cls(
            protagonist_name=protagonist.name,
            protagonist_location=protagonist.starting_location,
            inventory=list(protagonist.starting_inventory),
            summary=f"{scenario.setting}",
            config_name=config_name,
        )
```

## Agent Response Models

Each agent returns structured output. The Memory agent in particular must return validated JSON that maps to Pydantic models. Response schemas are defined in `src/models/responses.py`:

```python
class MemoryUpdate(BaseModel):
    """Structured output from the Memory agent."""
    characters: dict[str, Character] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    protagonist_location: str = ""
    inventory: list[str] = Field(default_factory=list)
    summary_update: str = ""  # only populated every N turns

class ConsistencyCheck(BaseModel):
    """Structured output from the Consistency agent."""
    flags: list[str] = Field(default_factory=list)
    reasoning: str = ""
```

## Agents

### Narrator

The creative core. Reads the user's command and writes the next story beat.

**Reads:** `user_input`, recent history (last N beats), `summary`, `characters`, `protagonist_location`, `inventory`

**Writes:** `current_narration` (1-3 paragraphs)

This is the ONLY output the user sees. It should advance the story meaningfully every turn. If the user says "go left," the story goes left.

### Consistency

The quality gate. Reviews the Narrator's output against world state and flags contradictions.

**Reads:** `current_narration`, `characters`, `locations`, recent history, `summary`

**Writes:** `consistency_flags`, `contradiction_count`

Does NOT rewrite narration. Only flags problems. If the config allows retries, the graph routes back to the Narrator with the flags.

### Director (silent)

Translates the narration into a cinematic scene description. Stored in state but never shown to the user. Exists for the future video pipeline (Wan I2V via DashScope).

**Reads:** `current_narration`, `protagonist_location`, `characters`

**Writes:** `scene_description`

### Memory

Maintains the world state. Extracts structured information from the narration. Returns a `MemoryUpdate` Pydantic model.

**Reads:** `current_narration`, `characters`, `locations`, `inventory`

**Writes:** updated `characters`, `locations`, `protagonist_location`, `inventory`

Every `summary_interval` turns, compresses old history into `summary`.

MERGES updates — if it doesn't mention a field, preserve the existing value.

## Prompt Templates

Agent prompts live as `.md` files in `src/prompts/`, loaded at runtime via a simple template loader (ported from Comic Chaos):

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

Each agent has a `system.md` and `user.md` template. Variables are substituted at call time:

```markdown
# narrator.user.md
PROTAGONIST: {protagonist_name} — currently at {protagonist_location}
INVENTORY: {inventory}

KNOWN CHARACTERS:
{characters_summary}

STORY SO FAR: {summary}

RECENT EVENTS:
{recent_beats}

USER'S COMMAND: {user_input}

Write the next 1-3 paragraphs of the story based on the user's command.
```

## JSON Sanitizer

LLMs will produce malformed JSON — especially local models like Gemma. The `json_sanitizer.py` module (ported from Comic Chaos) provides battle-tested repair:

- `sanitize_json_string(raw)` — pre-parse cleanup (null bytes, malformed unicode escapes)
- `extract_json(text)` — find first `{` to last `}` and try to parse
- `repair_json(text)` — truncate at last valid value boundary and close
- `sanitize_parsed_response(data)` — deep-clean all string values post-parse

Parse strategy for Memory agent responses:
1. Try direct `json.loads()`
2. If that fails, try `extract_json()`
3. If that fails, try `repair_json()`
4. If all fail, skip update for this turn and log the failure

## Interaction Logger

Every LLM call is logged to `logs/` as structured JSON (ported from Comic Chaos). Each session/run gets its own log file:

```json
{
  "session_id": "20260409_143022",
  "config": "mas_3_agent",
  "scenario": "mystery",
  "interactions": [
    {
      "type": "narrator",
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

This is essential for debugging agent behavior and for the benchmark post-analysis.

## Graph Topology

### 4-Agent (`mas_4_graph.py`)

```
User Input → Narrator → Consistency ─┬─ (clean) → Director → Memory → Output
                                      └─ (flags + retries left) → Narrator
```

### 3-Agent (`mas_3_graph.py`)

```
User Input → Narrator → Consistency ─┬─ (clean) → Memory → Output
                                      └─ (flags + retries left) → Narrator
```

### 2-Agent (`mas_2_graph.py`)

```
User Input → Narrator → Memory → Output
```

### Single LLM (`single_llm_graph.py`)

```
User Input → Single Agent → Output
```

## LLM Backend

Both Gemma 4 and OpenAI GPT-4o are called through the same interface via OpenAI-compatible APIs:

```python
class LLMBackend(ABC):
    async def generate(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024) -> str: ...
```

- **Gemma 4 31B** — vLLM on `localhost:8000`, OpenAI-compatible endpoint
- **GPT-4o** — OpenAI API, `OPENAI_API_KEY` from env

## Context Management

Agents are stateless between calls. Context is reconstructed each time:

1. **Sliding window** — last N story beats verbatim (configurable, default 5)
2. **Compressed summary** — older history condensed by the Memory agent every 10 turns
3. **World state snapshot** — current characters, locations, inventory serialized from Pydantic models

Target: ~4-8K tokens per agent call.

## Configuration

Configs are YAML files loaded into Pydantic models:

```yaml
name: "mas_3_agent"
description: "3-agent MAS: Narrator + Consistency + Memory"
graph: "mas_3_graph"
llm_backend: "gemma"
model: "google/gemma-4-31b-it"
temperature: 0.7
max_tokens_per_agent: 1024
consistency_retries: 1
summary_interval: 10
context_window_beats: 5
```

## Scenario Definition

Scenarios are JSON files loaded into Pydantic models:

```python
class Protagonist(BaseModel):
    name: str
    description: str
    starting_location: str
    starting_inventory: list[str]

class Turn(BaseModel):
    turn: int
    user_input: str

class Scenario(BaseModel):
    scenario_id: str
    title: str
    genre: str
    description: str
    setting: str
    protagonist: Protagonist
    test_focus: list[str]
    turns: list[Turn]
```
