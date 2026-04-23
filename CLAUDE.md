# CLAUDE.md

ClankerStudios — a four-role LLM workflow for interactive storytelling, benchmarked against a monolithic single-LLM baseline. Academic project for IKT469 at the University of Agder.

## Conventions

- **State & responses**: Pydantic v2 `BaseModel`. Not `TypedDict`, not raw dicts.
- **Agents**: async functions, not classes.
