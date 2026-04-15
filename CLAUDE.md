# CLAUDE.md — interactive-mas (IKT469)

## Workflow

- Enter plan mode for any non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, stop and re-plan — don't keep pushing
- When uncertain mid-task, stop and ask — never assume

## Elegance Check

For non-trivial changes, pause and ask "is there a more elegant way?" — skip for simple fixes.

## Project Notes

- State models: **Pydantic BaseModel** — not TypedDict, not raw dicts
- Prompts: **.md template files** in `src/prompts/`, loaded via `prompt_loader.py`
- Agents: **async functions**, not classes
- LLM JSON: parsed through **json_sanitizer** (try parse → extract → repair → fallback)
- LLM: local **vLLM** (OpenAI-compatible) — model via `MODEL_NAME` env var