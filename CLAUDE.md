# CLAUDE.md

Artificial Realities — a four-role LLM workflow for interactive storytelling: a beat writer (Tolkien), a shot composer (Spielberg), a voice-over commentator (Attenborough), and a memory curator (Spock) coordinate over a fixed LangGraph pipeline to turn user steering between clips into a continuous flowing video story. Under active evolution per `plans/` toward the direction in `docs/NORTH_STAR.md`.

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts.
- **Agents**: async functions, not classes.
