# Findings — frozen snapshots from the IKT469 school-project era

These docs preserve the empirical knowledge and architectural reasoning the academic project produced,
mined from artifacts that the cleanup deletes (`report/`, `docs/BENCHMARK.md`, `logs/judge/`) before
they're gone. They describe **what was true at the time of the IKT469 study (data ~2026-05-25)** — not
necessarily what's still true after later refactors.

**These files are mutable.** A future plan that changes something a finding described must **update the
finding doc**, not silently outdate it. New findings get spun out from `docs/EVOLUTION.md` (the quarry)
when a plan consumes the relevant section.

## Index

- **[four-role-architecture.md](four-role-architecture.md)** — the Tolkien/Spielberg/Attenborough/Spock
  decomposition: what each role owned/read/wrote, the load-bearing coordination contracts (one-turn-
  delayed feedback loop, end-frame chaining, role-disciplined slicing), the Director/Cinematographer/
  Editor research-consensus mapping, what the benchmark showed about the split, and transfer notes for
  the planned ~3-role redesign. **Read before redesigning the agents.**
- **[judge-methodology.md](judge-methodology.md)** — the cross-family LLM-as-judge approach (GPT-4.1
  generates, Claude Sonnet 4.6 judges), the two-layer 0–3 rubric (per-turn local + per-window memory),
  the pre-registered scenario as ground truth, the descriptive-not-inferential reporting stance, and the
  cost envelope. **Read before doing offline A/B evaluation** (the judge code survives — see the doc).
- **[benchmark-results.md](benchmark-results.md)** — the concrete numbers (the only surviving copy):
  the N=3 headline table, per-run window detail, representative per-phase local means, the
  interpretation, the honest limitations, and the full 7-phase scenario design.

## Deferred (not yet extracted)

Three planned findings docs were deferred because their source (`docs/EVOLUTION.md`) is being kept as
the quarry — copying them now would just create a sync burden. They'll be spun out when a plan consumes
the relevant EVOLUTION section *(see `plans/00-cleanup-baseline.md` §0.1):*
`bottlenecks.md` (EVOLUTION §A.3 B1–B8), `load-bearing-decisions.md` (§A.4), `positioning.md` (§1.x, §D).
