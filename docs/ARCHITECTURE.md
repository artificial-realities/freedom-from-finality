# Architecture

Artificial Realities generates a film as a sequence of short segments while a viewer watches and steers. This document describes the current orchestration, the **Director engine** (`graph: "director"`). The previous four-role engine is kept as `graph: "mas_graph"` for A/B comparison and is documented under [`docs/legacy/`](legacy/). The review that motivated the change is [`docs/reviews/2026-09-08-orchestration-review.md`](reviews/2026-09-08-orchestration-review.md).

## Design in one paragraph

Two LLM roles on two clocks, everything else deterministic. The **Director** makes one structured call per segment on the critical path and returns everything the segment needs: what happens, how it is shot, what is said, what changed in the world, and how the viewer's input was handled. The **Showrunner** runs off the critical path on triggers (scene boundaries, stagnation, a compaction interval) and does memory and planning in one call: it compacts the event log into summaries, records durable facts, prunes dead threads, and writes the next scene card. Code owns the rest: a typed world model that is the only source of truth, a validator that decides what the Director may change, a context assembler that decides what goes into the prompt, a prompt compiler that speaks the video model's dialect, pacing gates, and persistence.

```
viewer input ─▶ Engine (deterministic, asyncio)
                 · keeps every input · voice-over gate · stagnation ladder · pipelining · persistence
                     │ assemble context                         │ triggers: scene exit, stagnation,
                     ▼                                          │ replan flag, every N segments
        Context assembler (code)                                ▼
        bible · scene card · active entities · threads     Showrunner (LLM, slow clock)
        · last K beats · pending interventions · gates     compact log → summaries · cast notes
                     │                     ◀── plan + summaries land before next read ── · prune threads · arc · next scene card
                     ▼                                          ▲ reads log + state
        Director (LLM, once per segment)                        │
        beat · shots · dialogue/voice-over · entity ops         │
        · thread ops · viewer disposition · flags               │
                     │ proposes    ◀── reject, re-ask once      │
                     ▼                                          │
        Validator (code): schema · cast registry · entity existence · structural rules
                     │ apply                                    │
                     ▼                                          │
        World model (typed, persisted per segment) ────────────┘
        entities · threads · interventions · plan · event log · summaries
                     │ segment spec
                     ▼
        Prompt compiler (code) ─▶ LTX-2.5 server (continue / cut, native audio) ─▶ player
```

## Components

| Component | File | Kind | Responsibility |
|---|---|---|---|
| Engine | `src/graph/director_engine.py` | code | Turn sequence, Showrunner clock and joins, views for the runner, transcript notes |
| Director | `src/agents/director.py`, `src/prompts/director.*.md` | LLM | One `Segment` per turn; one re-ask on hard violations |
| Showrunner | `src/agents/showrunner.py`, `src/prompts/showrunner.*.md` | LLM | `ShowrunnerUpdate`: compaction + plan, on triggers |
| World model | `src/story/world.py` | code | Entities, threads, interventions, plan, event log, summaries; save/load |
| Validator | `src/story/validator.py` | code | `check` (pure) and `apply` (mutating) for segments; `apply_showrunner` for updates |
| Context assembler | `src/story/context.py` | code | Every prompt section, so the prompt budget is decided in one place |
| Prompt compiler | `src/story/compiler.py` | code | `Segment` → LTX-2.5 prompt text and total duration |
| Pacing | `src/story/pacing.py` | code | Voice-over gate, story clock, stagnation ladder, scene cap |
| Schemas | `src/story/schemas.py` | code | `Segment`, `ShotSpec`, `EntityOp`, `ThreadOp`, `DialogueLine`, `ShowrunnerUpdate` |

## The segment, step by step

1. **Join.** If a Showrunner task is in flight from the previous segment, await it and land its update (summaries, notes, pruned threads, arc, and a new scene card when it replanned).
2. **Record input.** A non-empty viewer line becomes an `Intervention` with disposition `pending`. Nothing is dropped: the live loop still coalesces lines typed between clips, but every line the engine sees is recorded.
3. **Bootstrap.** On the first segment the Showrunner runs synchronously to write the arc and the opening scene card from the blueprint.
4. **Gates.** The voice-over gate opens when no audio is owed and the silence floor (`min_pause_seconds`, in story seconds) has elapsed since the last line. The stagnation ladder turns "segments since anything structural changed" into prompt pressure at `stagnation_threshold` and a required shift at twice that. The scene cap forces `scene_done` at `max_segments_per_scene`.
5. **Director call.** The context assembler renders the bible (static, cache-friendly system prompt), the plan, the world now, open threads, the viewer section, summaries, the recent segments verbatim, and the gates. The Director returns a `Segment`.
6. **Validate and apply.** Hard violations (an unknown entity, a new character in a locked cast, dialogue from a mute character, a holder that is not a character) trigger exactly one re-ask with the violations spelled out. Then `apply` mutates the world: entity ops, presence from the shot list, thread ops, the intervention disposition. Unresolvable ops are dropped and logged as warnings, never silently.
7. **Compile.** The compiler turns the shots into one flowing prompt in the order LTX-2.5 responds to (sound, camera, who is on screen described from their sheet, the action, dialogue in quotes, the image the shot ends on). A `cut` re-establishes the shot and re-identifies the characters. When `narration: "ltx"`, the voice-over line is folded into the prompt for native audio.
8. **Views.** The engine fills the legacy `current_beat`, `current_shot`, `current_commentary` fields so the runner, playback, history and the transcript work unchanged for either engine. `current_transition` tells the renderer whether this segment is a cut.
9. **Persist.** The event record is appended and the world is saved to `logs/sessions/<session>/world.json` plus `events.jsonl`.
10. **Trigger.** `scene_done` or `needs_replan` → Showrunner in replan mode; stagnation past twice the threshold → replan; `compaction_interval` segments since the last compaction → compact mode. The task runs during rendering and is joined at step 1 of the next segment.

## World model tiers

| Tier | Where | Lifetime | In the Director prompt |
|---|---|---|---|
| Story bible | `data/story.json` → system prompt | immutable | always (rules, premise, style, cast sheets, locations, voice-over register) |
| World state | `WorldModel.entities` | current truth, mutated only by validated deltas | active entities in full; dormant props by id with `last_seen`; gone props by id |
| Scene context | `plan.scene`, last K events | one scene | always |
| Open threads | `WorldModel.threads` | until paid off or pruned | always, bounded |
| Interventions | `WorldModel.interventions` | whole run | current input, deferred requests still owed, the last few handled |
| Event log | `WorldModel.events` / `events.jsonl` | append-only | only the last `recent_beats` verbatim |
| Summaries | `WorldModel.summaries`, `act_summary` | derived, rebuildable | act summary + recent scene summaries |

Entities carry `kind` (character, prop, location), `description` (for the writer), `look` (verbatim for the renderer), `status`, `location`, `holder`, `present`, `gone`, `first_seen`/`last_seen`, `notes` (durable facts added by the Showrunner), `speaks`, and `reference_image`. `cast_locked` on the story makes introducing characters a hard violation; creatures and objects are props.

## Viewer influence

Input is absorbed at the lowest level that fits. The Director classifies each request as `absorbed` (it happens now), `deferred` (it needs setup; the reason is recorded and the request stays visible as owed), or `declined` (only when it breaks the rules of the world). A later segment can absorb a deferred request without new input. The Showrunner sees the whole intervention list and is told to make room for deferred requests in the next scene. Structural rule checks (cast lock, entity existence, mute characters) are the validator's; judgment calls stay with the Director, which is told to bend toward the spirit of a request rather than refuse.

## Narration

Narration is a capability, not an agent. `voiceover` is an optional Director field behind the deterministic gate. `narration: "ltx"` folds the line into the video prompt so LTX-2.5 renders it natively; `"tts"` sends it through ElevenLabs when `audio_enabled` and mixes it over the clip's native track; `"off"` removes narration from the film and closes the gate. The text is produced whenever the gate is open regardless of rendering, so headless runs behave like live runs.

## The video boundary

The renderer receives a compiled prompt, a duration, and a transition. `continue` conditions the next chunk on the previous chunk's tail (the server's sliding window). `cut` sends `reset_window` and the server conditions on the reference image instead, which bounds identity drift and is a normal cinematic device. A segment may hold up to `max_shots_per_segment` shots (LTX-2.5 handles up to three per generation); the compiler joins them with explicit cuts. Only the compiler knows the model's dialect; the `Segment` schema does not change when the model does.

## Configuration

```yaml
graph: "director"
narration: "ltx"               # ltx | tts | off
min_pause_seconds: 15.0        # silence floor before the voice-over gate reopens
recent_beats: 4                # verbatim segments in the Director prompt
max_shots_per_segment: 3
max_segments_per_scene: 12     # hard cap → scene_done → Showrunner replan
compaction_interval: 10        # Showrunner compaction cadence
stagnation_threshold: 5        # segments without structural change before pressure
director_model: ""             # optional per-role model overrides
showrunner_model: ""
```

`configs/director.yaml` is the headless config; `configs/director.live.yaml` drives LTX-2.5. The `mas.*.yaml` configs run the legacy engine.

## Testing

`python -m tests.run` runs the suite without pytest: unit tests for the world model, validator, compiler and pacing, plus end-to-end runs of both engines on the scripted mock backend (`llm_backend: "mock"`), which exercises the re-ask path, cuts, multi-shot segments, deferred interventions, compaction and replanning without an API key.

## Not built yet, by design

Observation of rendered frames feeding an entity-level visual memory; per-character reference images and keyframe conditioning at cuts; semantic retrieval over the event log; character knowledge and relationship graphs; a cinematic-language rewriter; multi-viewer input aggregation. See the review for the reasoning and the order.
