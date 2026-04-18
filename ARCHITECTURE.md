# Architecture

## Design Philosophy

The system tells an interactive story as a stream of short (~5s) video clips chained together via image-to-video (i2v). The user types natural-language guidance between clips; the story keeps flowing whether or not the user speaks.

A single well-briefed LLM can in principle do every job this system does. The hypothesis we are testing is that **a single LLM does not hold up over long horizons** — context balloons, facts drift, setups get dropped. Splitting the work across specialized agents with clear responsibilities and explicit communication should produce more coherent long-horizon storytelling at a tolerable latency cost.

The roster is deliberately lean: **three agents**, each owning exactly one job, with no overlap.

- **Tolkien** — the beat writer. Decides what happens in the next ~5s (action, dialogue, outcome) and keeps the narrative direction current.
- **Spielberg** — the shot composer. Translates Tolkien's beat into a concrete image-to-video prompt: camera, composition, motion, on-screen elements, continuity from the previous clip's last frame.
- **Supervisor** — the memory and context curator. After each turn, updates the structured world state and the rolling narrative memory. Before the next turn, hands Tolkien a filtered context brief — only what he needs to know right now.

We intentionally leave out agents that were in earlier drafts (Wilde for prose polish, Chekhov for thread tracking). Prose polish is no longer relevant — the output medium is video, not prose. Thread tracking is folded into the single `short_term_narrative` direction, which the user implicitly reshapes with every command.

All three agents emit **structured output** via Pydantic schemas. The `json_sanitizer` pipeline repairs malformed JSON from the local model before parsing.

## One-Turn-Delayed Feedback Loop

Supervisor's updates from turn N are available to Tolkien at turn N+1. Tolkien does not see his own turn's state update within the same turn — he sees it on the next one. If Tolkien references something slightly wrong this turn, it gets corrected on the next. Over ~5s clips, this is imperceptible.

This is a load-bearing design choice. It means:

- Agents never wait on each other mid-turn — the pipeline has a simple forward shape.
- Tolkien never sees his own state update in the same turn that produced it, which prevents self-reinforcing drift.
- The cost of a one-turn-late correction is negligible at ~5s cadence.

Every `.md` prompt template and every agent spec in this document assumes this loop. It is the single most important implicit contract between agents.

## Pipeline Buffer (Delay-as-Feature)

When video generation is live, the MAS runs **ahead of the viewer** by roughly 6 clips (~30s). At boot, the pipeline pre-generates ~30s of video before playback starts. After that, every user command is queued and lands on the next *unrendered* clip, not the currently-playing one.

This is not a technical wart — it is narrative smoothing:

- User says "jump in the pool" → the MAS does not hard-cut. Tolkien has 1–2 turns to ease into it (character walks toward pool, pauses at the edge, then jumps).
- The story keeps flowing during silence. The user is not prompted; they *optionally* steer.
- Abrupt tonal or logical shifts get absorbed across several clips instead of snapping.

Architectural consequences:

1. **Story must keep moving when the user is silent.** The MAS does not block on user input. If the next clip is due and the user has said nothing, Tolkien advances on the current `short_term_narrative`.
2. **User input enters a queue.** It is applied to the next unrendered clip at the time Tolkien composes that clip's beat. Earlier queued clips play out unmodified.
3. **Two clocks.** The MAS's internal clock (one turn = one clip in the build queue) and the viewer's subjective clock (~6 clips behind). The one-turn-delayed feedback loop refers to the internal clock.

For the benchmark we **bypass the buffer** and run synchronously turn-by-turn, logging prompts only — no actual clips are generated. The buffer is a runtime concern, not an evaluation concern.

## Story Blueprint

The story world is defined once in `story.json` — a blueprint that establishes everything the agents need before the first turn. All fields are static and read-only during a run.

```python
class Location(BaseModel):
    name: str
    description: str

class Character(BaseModel):
    name: str
    description: str

class Story(BaseModel):
    title: str
    synopsis: str
    visual_style: str
    locations: list[Location]
    characters: list[Character]           # includes protagonist as the first entry
    world_constraints: list[str]
    narrative_premise: str
    long_term_narrative: str              # single direction
    short_term_narrative: str             # single direction, seeded from blueprint

    @classmethod
    def from_json(cls, path: Path) -> "Story":
        return cls(**json.loads(path.read_text()))
```

Each field has a primary audience:

| Field | Who reads it |
|---|---|
| `title`, `synopsis` | All agents (context framing) |
| `visual_style` | Spielberg |
| `locations` | Spielberg (full); Tolkien (names only) |
| `characters` | Spielberg (full); Tolkien (names + one-line summary) |
| `world_constraints` | Tolkien (upfront self-check instruction) |
| `narrative_premise` | Tolkien, solo baseline |
| `long_term_narrative` | Tolkien (read + rarely updates) |
| `short_term_narrative` | Tolkien (read + updates every turn) |

We keep **exactly one** `long_term_narrative` and **exactly one** `short_term_narrative`. Experience from a prior project (Comic Chaos) showed that plural directions cluster and dilute focus. A single short-term direction is easily reshaped by the user's next command; a single long-term direction is sticky and only changes when the story fundamentally pivots.

There is no `tone_guidelines` field. Mood and feel are carried by `narrative_premise` (for Tolkien) and `visual_style` (for Spielberg). Spielberg infers visual tone from Tolkien's beat in combination with the static `visual_style` anchor.

## Scenario

The scenario is separate from the story. It is a bare JSON list of user command strings — no schema, no Pydantic model.

```json
["Wake up.", "Look around my quarters.", "..."]
```

Loaded directly in the runner:

```python
turns = json.loads(Path("test_scenario.json").read_text())
for turn_number, user_input in enumerate(turns, start=1):
    ...
```

The single benchmark scenario is 100 turns that test inventory persistence, character tracking, world rule consistency, location continuity, and long-horizon coherence — all woven into one continuous playthrough.

## State (Pydantic Models)

State is lean. Most of it is prose that agents read as context. Structured portions are kept small and mergeable.

```python
from pydantic import BaseModel, Field

class Beat(BaseModel):
    """Tolkien's structured output per turn."""
    action: str                              # physical events in this ~5s
    outcome: str                             # what has changed after this clip
    short_term_narrative: str                # updated direction for next turn
    long_term_narrative: str | None = None   # only set when the arc genuinely shifts

class Shot(BaseModel):
    """Spielberg's structured output per turn."""
    i2v_prompt: str                          # the full prompt fed to the i2v model
    location_name: str                       # one of the blueprint locations
    on_screen: list[str]                     # names of characters visible
    camera: str                              # shot type, angle, lens feel
    motion: str                              # what is moving in frame
    continuity: str                          # how this clip starts from the previous last frame
    end_frame_description: str               # what the final frame of this clip depicts

class WorldStateDelta(BaseModel):
    """Supervisor's structured state update, merged into StoryState.world_state."""
    characters: dict[str, dict] = Field(default_factory=dict)  # partial updates
    protagonist_location: str = ""                             # empty = unchanged
    inventory: list[str] | None = None                         # None = unchanged; list = replace
    notes: list[str] = Field(default_factory=list)             # freeform notable state

class MemoryUpdate(BaseModel):
    """Supervisor's structured output per turn."""
    world_state_delta: WorldStateDelta
    narrative_memory: str                    # rolling compressed prose, updated every turn
    context_brief: str                       # filtered context for Tolkien's next turn

class HistoryEntry(BaseModel):
    turn: int
    user_input: str
    beat: Beat
    shot: Shot

class StoryState(BaseModel):
    """Shared state for an interactive storytelling session."""
    # Current turn
    turn_number: int = 0
    user_input: str = ""
    current_beat: Beat | None = None
    current_shot: Shot | None = None

    # Persistent memory — maintained by Supervisor
    world_state: dict = Field(default_factory=dict)   # merged WorldStateDelta history
    narrative_memory: str = ""                        # rolling prose, updated every turn
    context_brief: str = ""                           # Supervisor's brief for Tolkien's next turn

    # Narrative direction — maintained by Tolkien
    long_term_narrative: str = ""
    short_term_narrative: str = ""

    # History
    history: list[HistoryEntry] = Field(default_factory=list)

    # Meta
    config_name: str = ""

    # Blueprint — set once at initialization
    title: str = ""
    synopsis: str = ""
    visual_style: str = ""
    locations: list[Location] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    world_constraints: list[str] = Field(default_factory=list)
    narrative_premise: str = ""

    def get_recent_history(self, count: int = 5) -> list[HistoryEntry]:
        return self.history[-count:]

    @classmethod
    def initialize(cls, story: Story, config_name: str) -> "StoryState":
        return cls(
            title=story.title,
            synopsis=story.synopsis,
            visual_style=story.visual_style,
            locations=list(story.locations),
            characters=list(story.characters),
            world_constraints=list(story.world_constraints),
            narrative_premise=story.narrative_premise,
            long_term_narrative=story.long_term_narrative,
            short_term_narrative=story.short_term_narrative,
            config_name=config_name,
        )
```

`narrative_memory` grows every turn but stays bounded: Supervisor compresses older content into higher-level strokes while recent events stay detailed. The prompt explicitly instructs rolling compression.

`world_state` accumulates by merging `WorldStateDelta`s. Fields left blank in a delta mean "unchanged." `inventory` is `None` for unchanged, a concrete list for replacement (simpler than computing add/remove sets inside the model).

## Agents

Each agent is an async function:

```python
async def agent_name(state: StoryState, llm: LLMBackend, config: Config, logger: InteractionLogger) -> dict
```

Returns a partial state dict that the graph merges.

### Tolkien — Narrator

The creative core. Writes the beat for this turn and keeps the narrative direction current.

**Receives (via prompt):**
- `narrative_premise`, `world_constraints`
- `long_term_narrative`, `short_term_narrative`
- Supervisor's `context_brief` (filtered slice — relevant characters, recent events, current location, relevant inventory)
- Location names (not full descriptions)
- Character names with one-line summaries (not full visual descriptions)
- `user_input` (possibly empty if the user is silent)

**Writes:** `current_beat` (`Beat`) which carries the action, the outcome, and updated `short_term_narrative`. Occasionally updates `long_term_narrative`. The protagonist does not speak, so there is no dialogue field — all expression is physical.

**Does NOT receive:** `visual_style`, full location or character descriptions, raw world_state. These are Spielberg's and Supervisor's domains.

Tolkien's prompt includes an explicit self-check: respect `world_constraints`, do not contradict `context_brief`. There is no reactive consistency gate — prevention is cheaper than cure, and Supervisor's one-turn-delayed corrections keep drift bounded.

On silent turns (empty `user_input`), Tolkien advances on `short_term_narrative` without stalling. Silence is normal.

### Spielberg — Shot Composer

The visual director. Turns Tolkien's beat into a concrete image-to-video prompt.

**Receives (via prompt):**
- `visual_style` (the permanent visual anchor)
- Full `locations` and `characters` from the blueprint
- `current_beat` (Tolkien's just-written output)
- The previous clip's `end_frame_description` (for continuity)
- Current `protagonist_location` (from `world_state`)

**Writes:** `current_shot` (`Shot`) with the i2v prompt, camera, composition, motion, on-screen roster, continuity note, and end-frame description.

Spielberg always re-anchors on the locked visual descriptors from the blueprint. The character looks the way the blueprint says; the background is the blueprint's white void. This is how visual consistency survives across 100 chained clips — not via a separate continuity agent, but via Spielberg's discipline of re-reading the source of truth every turn.

**Prop entry patterns.** When a new prop enters the scene, Spielberg picks one of two modes and reflects it in the `i2v_prompt`:

1. **Summon** — the prop simply appears in frame (a soft pop-in, or already present when the clip opens).
2. **Walk-in** — the minifigure walks a few steps out of frame and returns carrying the prop.

Mix both for visual variety. Which mode fits this turn is a judgment call based on the beat's pace and what's already on screen.

The scene never changes location — there is only the white void — so continuity between clips is always about what has changed in the character's pose, what props are present, and what's happening. The `end_frame_description` captures that so the next clip can pick up cleanly.

### Supervisor — Memory and Context Curator

Memory is Supervisor's whole identity. Two output shapes (structured state + prose memory), one cognitive act: *know what's true, surface what's relevant*.

**Receives (via prompt):**
- `current_beat` and `current_shot` (this turn's outputs)
- Current `world_state` (accumulated dict)
- Current `narrative_memory` (rolling prose)
- Recent history (last ~5 entries)

**Writes:** `MemoryUpdate` containing:
1. `world_state_delta` — structured merge applied to `world_state`. Partial updates only; unmentioned fields preserved.
2. `narrative_memory` — full replacement of the rolling prose memory. Supervisor is instructed to compress older events into high-level strokes while keeping recent events detailed. Targets a bounded length (configurable, default ~800 tokens).
3. `context_brief` — a filtered prose brief for Tolkien's next turn. Deliberately lean. Pulls from world_state and narrative_memory to surface only what Tolkien needs: who is present, where he is, what's in his hands, what direction the story is heading, any recent commitments that should influence the next beat.

The `context_brief` is the load-bearing mechanism for long-horizon coherence. A monolithic LLM at turn 80 has all context in one wall of tokens; solo degrades as that wall grows. Supervisor's brief stays roughly constant in size because it filters rather than accumulates.

## Turn Execution Order

Per turn, in order:

1. **Read user input** (may be empty).
2. **Tolkien** reads `context_brief` (from last turn's Supervisor), `short_term_narrative`, `long_term_narrative`, and user input. Writes `current_beat` + updated narrative direction.
3. **Spielberg** reads `current_beat`, `visual_style`, full blueprint `locations` + `characters`, previous `end_frame_description`. Writes `current_shot`.
4. **(Optional)** the i2v model renders a clip from `current_shot.i2v_prompt` + the previous clip's last frame. Skipped in benchmark mode and when `video_enabled=false`.
5. **Supervisor** reads `current_beat`, `current_shot`, current `world_state`, current `narrative_memory`, recent history. Writes `world_state_delta`, new `narrative_memory`, new `context_brief`.
6. **Commit** the history entry (`turn`, `user_input`, `beat`, `shot`).

The context Supervisor writes in step 5 is the context Tolkien reads in step 2 of the *next* turn. That delay is the one-turn-delayed feedback loop.

## Graph Topology

### Solo (`solo_graph.py`)

One LLM handles everything. It receives the **entire blueprint** — including full `locations` and `characters` descriptions, the premise, constraints, both narrative directions — and emits a single structured response containing `Beat`, `Shot`, and `MemoryUpdate` in one call. The question solo answers is "can a well-briefed single LLM match a decomposed pipeline?", not "can an unbriefed LLM?".

```
User Input → Solo (single structured response: beat + shot + memory update) → Output
```

### MAS (`mas_graph.py`)

Three specialized agents.

```
User Input
    → Tolkien (beat + short/long-term direction)
    → Spielberg (shot / i2v prompt)
    → Supervisor (world_state, narrative_memory, context_brief)
    → Output
```

Strictly sequential. Spielberg depends on Tolkien's beat; Supervisor depends on both. There is no retry loop — Tolkien respects rules upfront, Supervisor catches drift on the next turn.

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

Each agent has a `system.md` and `user.md`. Variables are substituted at call time. Agents only see the blueprint fields relevant to their job.

Each prompt ends with a schema block that describes the structured output the agent must produce. The schema block lists fields, types, and purpose in plain text — not a raw JSON schema dump.

## JSON Sanitizer

Local models (Gemma 4) will occasionally produce malformed JSON. The `json_sanitizer.py` module provides repair functions shared by all three agents and solo.

Reference implementation is in `reference/json_sanitizer.py`. Study it and adapt — keep only what this project needs.

Parse strategy for structured responses:

1. Try direct `json.loads()`.
2. If that fails, try `extract_json()` (pulls JSON out of surrounding text).
3. If that fails, try `repair_json()` (fixes common malformations).
4. If all fail, log the failure and skip the update for this turn.

Skipped updates mean the state stays unchanged for that turn. This is safer than partial application.

## Interaction Logger

Every LLM call is logged to `logs/` as structured JSON. Each session gets its own log file. This is the primary output for benchmark evaluation — logs are reviewed post-hoc by a human or an LLM, not scored by an automated pipeline.

Reference implementation is in `reference/interaction_logger.py`. Study and adapt.

A logged interaction captures everything needed to reconstruct a run:

```json
{
  "session_id": "20260418_143022",
  "config": "mas",
  "scenario": "test_scenario",
  "story": "LEGO Mars",
  "interactions": [
    {
      "agent": "tolkien",
      "turn": 5,
      "timestamp": "2026-04-18T14:30:45",
      "model": "google/gemma-4-31b-it",
      "parameters": {"temperature": 0.7, "max_tokens": 1024},
      "prompt": {"system": "...", "user": "..."},
      "response": {"raw": "...", "parsed": {"action": "...", "dialogue": [], "outcome": "...", "short_term_narrative": "..."}},
      "token_usage": {"prompt": 2100, "completion": 450},
      "latency_ms": 1230
    }
  ]
}
```

## LLM Backend

Both Gemma 4 and OpenAI GPT-4o are called through the same interface via OpenAI-compatible APIs:

```python
class LLMBackend(ABC):
    async def generate(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024) -> tuple[str, dict]:
        """Returns (response_text, token_usage)."""
```

- **Gemma 4 31B** — local vLLM on `localhost:8000`, OpenAI-compatible endpoint.
- **GPT-4o** — OpenAI API, `OPENAI_API_KEY` from env.

## Context Management

Agents are stateless between calls. Context is reconstructed each turn as prose.

- **Tolkien** — receives premise + constraints + long/short narrative + Supervisor's filtered `context_brief` + location names + character summaries + user input. Target ~2–4K tokens.
- **Spielberg** — receives `visual_style` + full blueprint locations/characters + Tolkien's beat + previous `end_frame_description` + current location. Target ~2–4K tokens.
- **Supervisor** — receives beat + shot + current world_state + current narrative_memory + recent history. Target ~3–6K tokens.
- **Solo** — receives the full blueprint + full rolling state + recent history + user input in one call. Target ~6–10K tokens and growing with run length.

Solo's context grows monotonically; the MAS agents' contexts stay bounded because Supervisor's `context_brief` filters rather than accumulates. This is the central bet of the architecture.

## Configuration

Configs are YAML files loaded into Pydantic models:

```yaml
name: "mas"
description: "Tolkien → Spielberg → Supervisor"
graph: "mas_graph"
llm_backend: "gemma"
model: "google/gemma-4-31b-it"
temperature: 0.7
max_tokens_per_agent: 1024
context_window_history: 5
narrative_memory_target_tokens: 800
video_enabled: false
video_buffer_clips: 6
```

`video_enabled: false` is the default for benchmark runs. When set `true`, `video_buffer_clips` controls how many clips are pre-generated before playback starts.
