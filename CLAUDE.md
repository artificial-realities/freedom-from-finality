# CLAUDE.md

Artificial Realities — a four-role LLM workflow for interactive storytelling: a beat writer (Tolkien) and shot composer (Spielberg) form the per-turn critical path, a voice-over commentator (Attenborough) runs on the audio clock, and a memory curator (Spock) runs as an async editor behind the turn — orchestrated by a deterministic turn engine that turns user steering between clips into a continuous flowing video story. Under active evolution per `plans/` toward the direction in `docs/NORTH_STAR.md`.

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts.
- **Agents**: async functions, not classes.
