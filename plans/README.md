# Plans

How we evolve Artificial Realities, post-IKT469. See `docs/NORTH_STAR.md` for the destination and `docs/EVOLUTION.md` for the full menu of options.

## Workflow

- **One numbered plan per topic** (`NN-topic.md`). Each plan is **self-contained** — a fresh session should execute it without reading the others.
- **Two layers.** This README is the *thin, living roadmap* (ordering + one-liners + dependencies). Detailed plans are authored **just-in-time**, one at a time, against the code as it actually is — never banked up front (the tech in `EVOLUTION.md` rots; later plans depend on how earlier ones reshape the code).
- **Re-validate before planning** anything tech-specific (models, latencies, prices). `EVOLUTION.md` is a 2026-05-24 snapshot.
- **Right-size the artifact.** Multi-session / high-coupling / risky work gets a plan doc. An evening's mechanical change just gets done (task list at most).
- **`EVOLUTION.md` is the quarry** — sections get spun out into plans over time; it's deleted only once fully consumed. **`docs/findings/`** preserves school-project knowledge as frozen, citeable snapshots; a plan that changes what a finding described must update the finding, not silently outdate it.
- Do **not** create stubs for future plans. Author them interactively when their turn comes.

## Roadmap (sequence, not detailed plans)

Lightest-risk-first; everything after 00 is reorderable. Items marked *(no-regret)* pay off regardless of direction; the rest are steered by `docs/NORTH_STAR.md`.

1. **`00-cleanup-baseline.md`** *(done 2026-06-11, merged to main)* — strip the academic/benchmark identity; preserve findings; keep the reusable scorer.
2. **No-regret infra** — ~~native structured outputs (EVOLUTION B5)~~ *(done — `01-structured-outputs.md`)*; ~~Langfuse dashboard / trace mining~~ *(done — `main.py stats <session_id>`, no plan doc per right-sizing)*. *(Agent-parallelism B2 is likely mooted by the move-2 redesign — skip as a standalone.)*
3. **Memory: Spock → off-the-shelf framework.** *(infra done — `02-memory-swap.md`: Mem0 mode shipped opt-in via `memory_backend: "mem0"`, default stays prose; 30-turn A/B = parity + 31% cost; the deciding long-horizon (100+ turn) A/B is folded into the agent-redesign plan.)*
4. **Agent redesign: 4 roles → ~3 literature-grounded roles.** *(done 2026-06-12 — `03-agent-redesign.md`: user chose the clock-split cut; Tolkien → Spielberg critical path, Attenborough on the audio clock, Spock async, LangGraph retired. The 100+-turn prose-vs-Mem0 A/B remains as a follow-up chunk on this design.)*
5. **Streaming substrate: LTXV autoregressive chunk-streaming.** North-star move 3; the big re-architecture (kills end-frame chaining + the 30s gate). Start with a **hands-on GPU spike** (chunk size vs. coherence vs. latency) before committing the runner rewrite.

### Pruned by the north star (do NOT plan)

Wan 2.7 swap · Nano Banana/FLUX end-frame authoring · clip-boundary pacing/span machinery (reworked, not extended). See `docs/NORTH_STAR.md` § "What this north star PRUNES".
