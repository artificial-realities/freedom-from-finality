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
- LLM: local **vLLM** (OpenAI-compatible) — model via config YAML
- Most agents receive and return **plain text** — only Sheldon (Memory) and Chekhov (Threads) output structured JSON
- Reference implementations in `reference/` — study and adapt critically, don't copy blindly
- Story world defined once in `story.json` as a blueprint (setting, protagonist, rules, premise)
- One benchmark scenario: `test_scenario.json` — 100 turns testing everything
- Agent names: Tolkien (Narrator), Wilde (Editor), Sherlock (Consistency), Sheldon (Memory), Chekhov (Threads)
- Constraints in `story.json` are split: `world_constraints` only to Sherlock, `tone_guidelines` only to Wilde. Tolkien gets neither and writes freely.
- Benchmark matrix: **solo (1) / core (3: Tolkien → Sherlock → Sheldon) / full_cast (5: Tolkien → Wilde → Sherlock → [Sheldon ∥ Chekhov])**. Sheldon and Chekhov run in parallel on clean narration. Sherlock's retry loop goes back to Tolkien → Wilde → Sherlock only.
