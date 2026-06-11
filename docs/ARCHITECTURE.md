# Architecture

## Design Philosophy

The system tells an interactive story as a stream of short (~5s) video clips chained together via image-to-video (i2v), layered with live voice-over commentary on top. The user types natural-language guidance between clips; the story keeps flowing whether or not the user speaks.

**What kind of system this is.** Four role-specialized LLMs coordinate through shared state over a fixed pipeline. In Anthropic's agents-vs-workflows terminology this is a **workflow**, not an agent system: control flow is deterministic, there is no tool use, and no LLM decides what happens next.

A single well-briefed LLM could in principle do every job this system does — emitting beat, shot, commentary, and memory in one structured response. Its strength would be **holistic coherence** (it sees everything in one context window); its weakness is **juggling** — producing all four outputs at once strains the model as context accumulates, and setups get dropped, facts drift, threads are lost.

Decomposition inverts this. Specialization trivializes juggling: each agent writes one thing. The cost is that each agent sees only a role-specific **fraction** of the total context, so coherence has to emerge from coordination — shared state, forward-passing between specialists, and Spock's one-turn-delayed brief — rather than from a single holistic view. The bet behind this design is that the long-horizon payoff from specialization, as context strains the model, outweighs the coordination cost.

The roster is **four agents**, each owning exactly one job, with no overlap.

- **Tolkien** — the beat writer. Decides what happens in the next ~5s (prose narration, mechanical action, outcome) and keeps the narrative direction current.
- **Spielberg** — the shot composer. Translates Tolkien's beat into a concrete image-to-video prompt: camera, composition, motion, on-screen elements, continuity from the previous clip's last frame.
- **Attenborough** — the voice-over commentator. Reads Tolkien's beat and Spielberg's shot and writes the spoken line that plays over the clip. Always emits text; optionally fires ElevenLabs TTS when audio is enabled.
- **Spock** — the memory and context curator. After each turn, updates the structured world state and the rolling narrative memory. Before the next turn, hands Tolkien a filtered context brief — only what he needs to know right now, including the currently-relevant characters and locations.

All four agents emit **structured output** via Pydantic schemas, enforced at the API level with OpenAI native structured outputs (`response_format: json_schema`). Beat, Shot, and Commentary use strict mode (non-conforming JSON is impossible); MemoryUpdate uses non-strict schema guidance because its free-form `characters` dict can't be expressed in the strict subset — still guaranteed-valid JSON, with Pydantic validating downstream. The `json_sanitizer` pipeline remains as a thin fallback (e.g., `max_tokens` truncation).

## One-Turn-Delayed Feedback Loop

Spock's updates from turn N are available to Tolkien at turn N+1. Tolkien does not see his own turn's state update within the same turn — he sees it on the next one. If Tolkien references something slightly wrong this turn, it gets corrected on the next. Over ~5s clips, this is imperceptible.

This is a load-bearing design choice. It means:

- Agents never wait on each other mid-turn — the pipeline has a simple forward shape.
- Tolkien never sees his own state update in the same turn that produced it, which prevents self-reinforcing drift.
- The cost of a one-turn-late correction is negligible at ~5s cadence.

Every `.md` prompt template and every agent spec in this document assumes this loop. It is the single most important implicit contract between agents.

## Pipeline Buffer (Delay-as-Feature)

When video is live, the MAS *can* run ahead of the viewer by a configurable number of clips. The knob is `lead_clips` (default `0`). At `0`, the producer renders one clip, the player starts as soon as it is ready, and render+playback overlap by one clip naturally. At `lead_clips: N > 0`, the producer maintains an N-clip lead, the consumer waits for `N + 1` clips before launching ffplay, and every user command lands on the next *unrendered* clip instead of the currently-playing one.

The default is `0` because i2v latency today (~30s for a 5s clip on DashScope) makes a sustained lead expensive in wall time. The knob exists so the design re-engages once i2v gets fast enough, without code changes.

When the lead is in use, it is narrative smoothing, not a technical wart:

- User says "jump in the pool" → the MAS does not hard-cut. Tolkien has 1–2 turns to ease into it (character walks toward pool, pauses at the edge, then jumps).
- The story keeps flowing during silence. The user is not prompted; they *optionally* steer.
- Abrupt tonal or logical shifts get absorbed across several clips instead of snapping.

Architectural consequences (regardless of lead value):

1. **Story must keep moving when the user is silent.** The MAS does not block on user input. If the next clip is due and the user has said nothing, Tolkien advances on the current `short_term_narrative`.
2. **User input enters a queue.** It is applied to the next unrendered clip at the time Tolkien composes that clip's beat. Earlier queued clips play out unmodified. At `lead_clips: 0` this is "the next clip after the one currently playing"; at higher leads, several clips deeper.
3. **Two clocks.** The MAS's internal clock (one turn = one clip in the build queue) and the viewer's subjective clock (lead clips behind). The one-turn-delayed feedback loop refers to the internal clock.

The headless dev loop (`play`, no video) and scripted `play --scenario` runs **bypass the buffer entirely** and run synchronously turn-by-turn — no actual clips or audio are generated. The buffer is a live-playback concern only.

There is also a no-video live mode (`live: true, video_enabled: false`): the story still flows continuously and the user still types in the terminal, but instead of an ffplay window the four agents' outputs stream into a Tk popup as they're produced. No clips, no buffer, no lead — turns advance at LLM latency.

## Story Blueprint

The story world is defined once in `data/story.json` — a blueprint that establishes everything the agents need before the first turn. All fields are static and read-only during a run.

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
    tone_guidelines: str                  # voice/register anchor for Attenborough's commentary
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
| `tone_guidelines` | Attenborough |
| `locations` | Spielberg (full); Spock (full, for use in `context_brief`) |
| `characters` | Spielberg (full); Spock (full, for use in `context_brief`); Tolkien (protagonist entry only — always visible) |
| `world_constraints` | Tolkien (upfront self-check instruction) |
| `narrative_premise` | Tolkien |
| `long_term_narrative` | Tolkien (read + rarely updates) |
| `short_term_narrative` | Tolkien (read + updates every turn) |

We keep **exactly one** `long_term_narrative` and **exactly one** `short_term_narrative`. Experience from a prior project (Comic Chaos) showed that plural directions cluster and dilute focus. A single short-term direction is easily reshaped by the user's next command; a single long-term direction is sticky and only changes when the story fundamentally pivots.

`tone_guidelines` is Attenborough's exclusive anchor — it shapes his voice, cadence, and register (dry, earnest, deadpan, nature-documentary, etc.) without needing a re-brief every turn. Spielberg and Tolkien stay unaware of it; their tonal signals come from `visual_style` and `narrative_premise` respectively. Coordination between Spielberg and Attenborough happens implicitly through `Beat.narration` — both agents read the same prose source, so the visual and the voice land together without the agents talking to each other.

## Scenario

A scenario is separate from the story: a bare JSON list of user command strings — no schema, no Pydantic model. Passing one to `play --scenario` drives the session non-interactively, turn by turn.

```json
["Wake up.", "Look around my quarters.", "..."]
```

Loaded directly in the runner:

```python
turns = json.loads(Path("data/demo_scenario.json").read_text())
for turn_number, user_input in enumerate(turns, start=1):
    ...
```

`data/demo_scenario.json` is the canonical scripted demo input — a continuous playthrough useful for reproducible, hands-off runs.

## State (Pydantic Models)

State is lean. Most of it is prose that agents read as context. Structured portions are kept small and mergeable.

```python
from pydantic import BaseModel, Field

class Beat(BaseModel):
    """Tolkien's structured output per turn."""
    narration: str                           # 2–4 sentence prose paragraph telling what happens in this ~5s
    action: str                              # physical events, mechanical (for Spielberg)
    outcome: str                             # what has changed after this clip (hint for Spock)
    short_term_narrative: str                # updated direction for next turn
    long_term_narrative: str | None = None   # only set when the arc genuinely shifts

class Shot(BaseModel):
    """Spielberg's structured output per turn."""
    i2v_prompt: str                          # the full prompt fed to the i2v model
    on_screen: list[str]                     # names of characters visible
    camera: str                              # shot type, angle, lens feel
    motion: str                              # what is moving in frame
    end_frame_description: str               # what the final frame of this clip depicts

class Commentary(BaseModel):
    """Attenborough's structured output per turn."""
    voiceover: str                           # spoken text — ~1–3 short sentences, paced for ~5s of audio

class WorldStateDelta(BaseModel):
    """Spock's structured state update, merged into StoryState.world_state."""
    characters: dict[str, dict] = Field(default_factory=dict)  # partial updates
    protagonist_location: str = ""                             # empty = unchanged
    inventory: list[str] | None = None                         # None = unchanged; list = replace

class MemoryUpdate(BaseModel):
    """Spock's structured output per turn."""
    world_state_delta: WorldStateDelta
    narrative_memory: str                    # rolling compressed prose, updated every turn
    context_brief: str                       # filtered context for Tolkien's next turn (incl. relevant chars/locs)

class HistoryEntry(BaseModel):
    turn: int
    user_input: str
    beat: Beat
    shot: Shot
    commentary: Commentary

class StoryState(BaseModel):
    """Shared state for an interactive storytelling session."""
    # Current turn
    turn_number: int = 0
    user_input: str = ""
    current_beat: Beat | None = None
    current_shot: Shot | None = None
    current_commentary: Commentary | None = None

    # Persistent memory — maintained by Spock
    world_state: dict = Field(default_factory=dict)   # merged WorldStateDelta history
    narrative_memory: str = ""                        # rolling prose, updated every turn
    context_brief: str = ""                           # Spock's brief for Tolkien's next turn

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
    tone_guidelines: str = ""
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
            tone_guidelines=story.tone_guidelines,
            locations=list(story.locations),
            characters=list(story.characters),
            world_constraints=list(story.world_constraints),
            narrative_premise=story.narrative_premise,
            long_term_narrative=story.long_term_narrative,
            short_term_narrative=story.short_term_narrative,
            config_name=config_name,
        )
```

`narrative_memory` grows every turn, but the growth is smart: Spock compresses older content into higher-level strokes while recent events stay detailed. The prompt explicitly instructs rolling compression. There is no hard token cap — the prompt carries a soft target (`narrative_memory_target_tokens` from config) that Spock is told to drift toward, not enforce.

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
- The protagonist entry from `characters[0]` (name + description) — always visible, since he writes about this character every turn
- Current `world_state` (what's true right now: location, inventory, character states) — read directly, not via Spock's digest
- Current `narrative_memory` (rolling prose of what has happened across the whole run — grows with the story, compressed older / detailed recent per Spock's discipline)
- Recent `Beat.narration` from the last ~3 history entries (raw prose — for local detail and phrasing variety, so recent setups and exact language aren't lost in `narrative_memory`'s compression; symmetric with Attenborough's recent-commentary read)
- Spock's `context_brief` (a narrow attention pointer across the one-turn delay — surfaces which OTHER characters from the blueprint are currently in scene with one-line summaries, plus any recent commitments worth honoring. Does not rebuild the world; Tolkien reads `world_state` directly)
- `user_input` (possibly empty if the user is silent)

**Writes:** `current_beat` (`Beat`) carrying `narration` (2–4 sentence prose paragraph — the richest downstream source), `action` (mechanical events for Spielberg), `outcome` (state-delta hint for Spock), and the updated `short_term_narrative`. Occasionally updates `long_term_narrative`.

**Does NOT receive:** `visual_style`, `tone_guidelines`, full non-protagonist character or location descriptions. These would cause bleed-through into other agents' domains — Tolkien seeing `tone_guidelines` starts writing in Attenborough's voice; seeing `visual_style` starts doing cinematography. State slicing has been relaxed (he reads `world_state` directly); only this role-discipline slicing remains.

Tolkien's prompt includes an explicit self-check: respect `world_constraints`, do not contradict `context_brief`. There is no reactive consistency gate — prevention is cheaper than cure, and Spock's one-turn-delayed corrections keep drift bounded.

On silent turns (empty `user_input`), Tolkien advances on `short_term_narrative` without stalling. Silence is normal.

**Turn 1 bootstrap.** At turn 1 there is no prior Spock output, so `context_brief` is empty. Tolkien opens the story himself using the blueprint material he already has (premise, constraints, narrative directions, protagonist entry, user_input). This is deliberate: Tolkien is the creative lead and is in control of how the story starts.

### Spielberg — Shot Composer

The visual director. Turns Tolkien's beat into a concrete image-to-video prompt.

**Receives (via prompt):**
- `visual_style` (the permanent visual anchor)
- Full `locations` and `characters` from the blueprint
- `current_beat` (Tolkien's just-written output — especially the prose `narration`)
- The previous clip's `end_frame_description` (for continuity)
- Current `world_state` — especially `protagonist_location` (defaults to `locations[0].name` when unset) and `inventory`, so worn and carried props stay in frame even when Tolkien's current beat doesn't re-mention them

**Writes:** `current_shot` (`Shot`) with the i2v prompt, camera, motion, on-screen roster, and end-frame description.

Spielberg always re-anchors on the locked visual descriptors from the blueprint every turn. The character looks the way the blueprint says; the setting is described as the blueprint specifies. This is how visual consistency survives across 100 chained clips — not via a separate continuity agent, but via Spielberg's discipline of re-reading the source of truth every turn.

How props enter, scenes transition, and characters arrive is left to Spielberg's judgment, guided by `visual_style` and `narrative_premise`. A studio-white-void story calls for one set of entrance conventions; a gritty cyberpunk story calls for another. Spielberg is the director — he figures it out from the blueprint. The architecture does not hardcode entrance patterns.

### Attenborough — Voice-Over Commentator

The narrator-in-the-mix. Writes the spoken line that plays over one or more clips — in whatever register `tone_guidelines` asks for. Pacing is his to decide: silence, a short line, or a longer continuous thought.

**Receives (via prompt):**
- `tone_guidelines` (the voice anchor)
- `current_beat` (especially the prose `narration`)
- `current_shot` (camera + motion + end-frame, so the commentary lands on visible action)
- `short_term_narrative` (Tolkien's direction for the NEXT beat — the look-ahead that tells Attenborough whether a longer line is safe)
- Current `narrative_memory` (rolling prose — so he can spot callbacks and pace his voice against the arc, not just the current clip)
- Recent `HistoryEntry.commentary` from the last ~5 turns (empty entries mean silence; used to avoid repeating himself and to feel the rhythm)
- `silence_seconds` (how long since the last voiceover finished — a hint that the room is open, not a command to fill it)

**Writes:** `current_commentary` (`Commentary`) with one field:
- `voiceover` — a string, possibly empty. Empty is a first-class choice.

**The measured-audio pacing model.** Attenborough never predicts how many clips his line will cover. The live producer instead:
1. Sends his `voiceover` through ElevenLabs and probes the resulting MP3 duration (`audio_dur`).
2. Probes the freshly-rendered i2v clip duration (`clip_dur`).
3. If `audio_dur > clip_dur`, opens a **pending span**: collects subsequent silent clips until their accumulated duration covers the audio, then concatenates them and muxes the single voiceover across the whole span (`concat_videos_and_mux_audio`).
4. Sets `StoryState.audio_seconds_owed = audio_dur - clip_dur` so the next turn's Attenborough call sees the gate is closed and skips the LLM entirely.
5. Tracks `StoryState.silence_seconds` between voiceovers and enforces `Config.min_pause_seconds` as a floor — a forced cinematic pause so lines do not stack back-to-back.

Both gates are only enforced when `state.pacing_managed` is True (set by the live producer at startup) and `audio_enabled` is True. The scenario and interactive loops always reach the LLM so commentary text is produced for every turn — the gates only affect playback in the live demo.

On a held turn Attenborough never reaches his prompt; the empty `Commentary` is logged as an `attenborough_hold` event with the reason (`audio_owed` or `min_pause`).

Coordination with Spielberg happens implicitly: both read the same `Beat.narration`. The visual and the voice land together because they come from the same prose source.

**Audio side effect.** After Attenborough produces text, the runtime optionally sends `voiceover` to ElevenLabs TTS when `audio_enabled: true`. When `audio_enabled: false` (the default), the text is logged and nothing else happens. This mirrors the video toggle — the agent's structured output is the artifact that matters; audio and video generation are runtime niceties. TTS failures are caught and logged — they never break the turn.

### Spock — Memory and Context Curator

Memory is Spock's whole identity. One cognitive act: *know what's true, surface what's relevant*. His output shape depends on `memory_backend`:

- **`prose`** (legacy): structured state delta + full-replacement rolling `narrative_memory` + the filtered brief.
- **`mem0`** (plan 02): structured state delta + the brief only. The episodic layer lives in an embedded Mem0 store — a post-turn graph node persists each beat (custom narrative extraction instructions keep props/bits/setups), and Tolkien/Attenborough retrieve *relevant* memories per turn instead of reading one rolling blob. The structured `world_state` ledger stays first-party in both modes.

**Receives (via prompt):**
- `current_beat`, `current_shot`, `current_commentary` (this turn's outputs)
- Current `world_state` (accumulated dict)
- Current `narrative_memory` (rolling prose)
- Full blueprint `locations` and `characters` (so Spock can pull relevant names + one-line summaries into `context_brief` on demand)
- Recent history (last ~5 entries)

**Writes:** `MemoryUpdate` containing:
1. `world_state_delta` — structured merge applied to `world_state`. Partial updates only; unmentioned fields preserved.
2. `narrative_memory` — full replacement of the rolling prose memory. Spock is instructed to compress older events into high-level strokes while keeping recent events detailed. Drifts toward `narrative_memory_target_tokens` from config (soft target, not a hard cap).
3. `context_brief` — a narrow attention pointer for Tolkien's next turn. Not a world rebuild: Tolkien reads `world_state` and `narrative_memory` directly, so the brief only surfaces **which other characters from the blueprint are currently in scene** (names + one-line summaries pulled from the blueprint, because `world_state` carries character names but not their descriptions) and **any recent commitments or setups** that should influence the next beat. Location, inventory, and narrative direction Tolkien reads directly; the brief does not repeat them.

The `context_brief` is the coordination artifact across the one-turn delay. Its job is narrow: direct Tolkien's attention to characters-currently-in-scene (whose descriptions live in the blueprint, not in `world_state`) and recent commitments worth honoring. The brief should stay light even as story complexity grows — extra context belongs in `world_state` (structured) and `narrative_memory` (rolling prose), both of which Tolkien reads on his own.

## Turn Execution Order

Per turn, in order:

1. **Read user input** (may be empty).
2. **Tolkien** reads `context_brief` (from last turn's Spock), current `world_state`, `narrative_memory`, recent `Beat.narration` from the last ~3 history entries, `short_term_narrative`, `long_term_narrative`, protagonist entry, and user input. Writes `current_beat` + updated narrative direction.
3. **Spielberg** reads `current_beat`, `visual_style`, full blueprint `locations` + `characters`, current `world_state` (esp. `protagonist_location`, `inventory`), previous `end_frame_description`. Writes `current_shot`.
4. **(Optional)** the i2v model renders a clip from `current_shot.i2v_prompt` + the previous clip's last frame. Skipped when `video_enabled: false`.
5. **Attenborough** reads `tone_guidelines`, `current_beat`, `current_shot`, `narrative_memory`, recent commentary history. Writes `current_commentary`.
6. **(Optional)** ElevenLabs TTS renders `current_commentary.voiceover`. Skipped when `audio_enabled: false`.
7. **Spock** reads `current_beat`, `current_shot`, `current_commentary`, current `world_state`, current `narrative_memory`, full blueprint chars/locs, recent history. Writes `world_state_delta`, new `narrative_memory`, new `context_brief`.
8. **Commit** the history entry (`turn`, `user_input`, `beat`, `shot`, `commentary`).

The context Spock writes in step 7 is the context Tolkien reads in step 2 of the *next* turn. That delay is the one-turn-delayed feedback loop.

## Graph Topology

Four specialized agents wired in `mas_graph.py`.

```
User Input
    → Tolkien       (beat: narration + action + outcome + short/long-term direction)
    → Spielberg     (shot / i2v prompt)
    → Attenborough  (voice-over commentary)
    → Spock         (world_state, narrative_memory, context_brief)
    → Output
```

Strictly sequential. Spielberg depends on Tolkien's beat; Attenborough depends on both; Spock depends on all three. There is no retry loop — Tolkien respects rules upfront, Spock catches drift on the next turn.

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

## JSON Sanitizer (fallback layer)

Agent responses are constrained by native structured outputs, so malformed JSON from the OpenAI path is near-impossible. `src/util/json_sanitizer.py` survives as a **fallback layer**, not load-bearing infra: it still guards the edge cases (`max_tokens` truncation mid-object) and is the primary parse path for the Anthropic judge in `src/eval/judge.py`, which has no equivalent structured-output mode.

Parse strategy (unchanged):

1. Try direct `json.loads()`.
2. If that fails, try `extract_json()` (pulls JSON out of surrounding text).
3. If that fails, try `repair_json()` (fixes common malformations).
4. If all fail, log the failure and skip the update for this turn.

Skipped updates mean the state stays unchanged for that turn. This is safer than partial application.

## Interaction Logger

Every LLM call is logged to `logs/` as structured JSON. Each session gets its own log file. This is the primary record of a run — reviewable post-hoc by a human or by the LLM-as-judge scorer in `src/eval/judge.py`. Implementation lives in `src/util/story_log.py`.

A logged interaction captures everything needed to reconstruct a run:

```json
{
  "session_id": "20260418_143022",
  "config": "mas",
  "scenario": "test_scenario",
  "story": "Lego Man in the White Void",
  "interactions": [
    {
      "agent": "tolkien",
      "turn": 5,
      "timestamp": "2026-04-18T14:30:45",
      "model": "gpt-4.1",
      "parameters": {"temperature": 0.7, "max_tokens": 1024},
      "prompt": {"system": "...", "user": "..."},
      "response": {"raw": "...", "parsed": {"narration": "...", "action": "...", "outcome": "...", "short_term_narrative": "..."}},
      "token_usage": {"prompt": 2100, "completion": 450},
      "latency_ms": 1230
    }
  ]
}
```

## LLM Backend

Every agent calls OpenAI through a single `LLMBackend` interface. The abstract base exists so a second backend can be slotted in later without touching agent code, but the only concrete implementation is `OpenAIBackend`.

```python
class LLMBackend(ABC):
    async def generate(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024) -> tuple[str, dict]:
        """Returns (response_text, token_usage)."""
```

- **OpenAI** — `OPENAI_API_KEY` from env; the model is set per config (`model:` in the YAML).

## Context Management

Agents are stateless between calls. Context is reconstructed each turn as prose.

- **Tolkien** — receives premise + constraints + long/short narrative + protagonist entry + current `world_state` + `narrative_memory` + recent `Beat.narration` from last ~3 history entries + Spock's `context_brief` + `user_input`. On turn 1 the `context_brief`, `narrative_memory`, and history are empty and `world_state` is default; Tolkien opens from the blueprint alone.
- **Spielberg** — receives `visual_style` + full blueprint locations/characters + Tolkien's beat (including `narration`) + previous `end_frame_description` + current `world_state` (esp. `protagonist_location` and `inventory`).
- **Attenborough** — receives `tone_guidelines` + `current_beat` (especially `narration`) + `current_shot` + `narrative_memory` + recent commentary history.
- **Spock** — receives beat + shot + commentary + current `world_state` + current `narrative_memory` + full blueprint chars/locs + recent history.

Slicing in the MAS is **deliberately narrow and role-disciplined**, not state-hiding. `visual_style` stays with Spielberg and `tone_guidelines` stays with Attenborough so Tolkien doesn't start writing cinematography or voice-over — this prevents bleed-through into other agents' domains. But the shared rolling state (`world_state`, `narrative_memory`) is not sliced: every agent that would benefit from the current world reads it directly. Each agent reads a comparable slice for its role but only has to write one thing. Spock's `context_brief` is a narrow attention pointer across the one-turn delay — it flags which characters are in scene and which recent commitments matter, rather than rebuilding the world. The central bet is that **task specialization holds up as context strains the model**, where monolithic single-pass handling drops setups and loses threads.

## Configuration

Configs are YAML files loaded into Pydantic models:

```yaml
name: "mas"
description: "Tolkien → Spielberg → Attenborough → Spock"
graph: "mas_graph"
llm_backend: "openai"
model: "gpt-4.1"
temperature: 0.7
max_tokens_per_agent: 1024
context_window_history: 5
narrative_memory_target_tokens: 800
live: false                      # true → continuous live loop; false → sequential `run_play`
video_enabled: false
lead_clips: 0                    # producer lead in clips (0 = no startup wait)
audio_enabled: false
elevenlabs_voice_id: ""          # required when audio_enabled: true
```

`video_enabled: false` and `audio_enabled: false` are the defaults for headless dev runs. When `audio_enabled: true`, Attenborough's `voiceover` is sent to ElevenLabs and audio files are written to `logs/audio/`; when `false`, the text is still produced, still logged, and simply not spoken — keeping dev runs cheap and reproducible.
