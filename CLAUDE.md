# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first.** It is the source of truth for this project: what it is, the repository map, setup and test commands, secrets handling, the rules on paid OpenAI calls and rented GPUs, the conventions and how the film behaves. This file only adds Claude Code workflow preferences on top of it.

## Workflow

- **Plan first** for non-trivial work (3+ steps or an architectural decision). Share the plan and check in before implementing, unless told to go ahead.
- **Verify before calling it done.** Run `ruff check .` and `python -m pytest`. Never start a film with a real model, render on a GPU or rent one to verify something: that costs money and needs the owner's explicit go-ahead.
- **Keep the design's split.** The LLM roles propose, code validates and applies; prompts, schemas, compiler and engine stay story-agnostic; no code judges story quality. When something about the film's behaviour should change, prefer giving the roles better facts over adding a rule in code.
- **Record decisions, not experiments.** A finding that changes the design goes into `docs/decisions.md` in a few lines with its numbers; experiment code stays out of the repository.
- **Subagents** are fine for broad research or exploration that would flood the main context. Give each one a single focused task.
