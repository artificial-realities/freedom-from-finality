# CLAUDE.md — interactive-mas (IKT469)

## Workflow

- When uncertain mid-task, stop and ask — never assume
- For non-trivial changes, pause and ask "is there a more elegant way?" — skip for simple fixes.

## Project Notes

- State models: **Pydantic BaseModel** — not TypedDict, not raw dicts
- Prompts: **.md template files** in `src/prompts/`, loaded via `prompt_loader.py`
- Agents: **async functions**, not classes
- LLM: local **vLLM** (OpenAI-compatible) — model via config YAML
- Reference implementations in `reference/` — study and adapt critically, don't copy blindly