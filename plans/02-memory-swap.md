# Plan 02 — Memory swap: Spock's hand-rolled summarizer → off-the-shelf framework

> **Status:** executed 2026-06-11. **Decisions taken (user, 2026-06-11): Mem0;
> quick-check A/B.** Results in §7.
>
> **Consumes:** `docs/EVOLUTION.md` §1.2 item 4, §A.3 B3+B4, §C.4/C.7 memory-
> framework notes. North-star move 1 (`docs/NORTH_STAR.md`).
>
> **Updates findings:** `docs/findings/four-role-architecture.md` (Spock's
> contract changes) — do this during implementation, not before.

---

## 1. Why (the bottlenecks this kills)

- **B3 — full-replacement `narrative_memory`:** Spock rewrites the entire prose
  memory every turn (~800-token output). Costliest agent confirmed live by
  `main.py stats` (2026-06-11 smoke: Spock $0.0273 of $0.0829, 10.9K input
  tokens over 5 turns). The rewrite is also a coherence hazard: Spock can
  overwrite earlier-correct details that left the 5-turn window — the
  structural reason late-game callback scores dipped in the benchmark.
- **B4 — 5-entry history window:** recall across 100+ turns depends entirely on
  Spock's prose compression keeping the right needle in the haystack. Retrieval
  over the full history removes that single point of failure.
- **North star:** off-the-shelf memory unlocks 100-turn → 1000-turn →
  **multi-session** continuity ("the show remembers things you said three
  weeks ago"), which separates demo from habit.

## 2. Candidates (validated live 2026-06-11 — supersedes EVOLUTION's 05-24 snapshot)

### Mem0 (recommended)

Hands-on spike ran in this repo (embedded `AsyncMemory`, OpenAI key from `.env`):

- **Fit:** in-process, async-native, no server. Default stack: embedded Qdrant
  (`/tmp/qdrant` — relocate to `logs/` or `data/`), SQLite history, OpenAI
  LLM + embedder. Matches the "agents are async functions" ethos exactly.
- **Measured:** `add` ≈ 1.6 s/turn (one extraction LLM call); `search` ≈ 0.2 s.
  Since reads happen *next* turn, the write can run as a post-turn side effect
  overlapped with render — **the one-turn-delayed feedback contract survives
  unchanged**, and search is cheap enough to call per-agent.
- **Retrieval quality:** story-shaped probes ("Where is the soccer ball?")
  returned exactly the right memories with sane scores. v2.0.5 has hybrid
  retrieval (semantic + BM25 + entity) — BM25/entity need optional extras
  (`fastembed`, `mem0ai[nlp]`); valuable for exact prop-name matching.
- **Risks found in the spike (both mitigable):**
  1. **Silent memory loss** — the default fact-extraction prompt judged a
     story beat (the triumphant hop, a future callback target) "not
     memorable" and extracted nothing. Mitigation: custom extraction `prompt`
     (supported parameter) tuned for narrative events / props / bits, or
     `infer=False` raw-beat storage alongside inferred facts.
  2. **Conversation-assistant bias** — memories get date-stamped ("on June
     11, 2026") and phrased as user facts. Same mitigation: custom prompt.
  3. Default LLM config 400s on newer OpenAI models (`max_tokens` vs
     `max_completion_tokens`) — pin `{"model": "gpt-4.1"}` explicitly.

### Letta (fallback, not first pick)

- The full platform is a **server-based agent runtime** (REST on :8283,
  `letta-client` SDK, memory blocks). Self-hosting means running a server —
  no Docker in the current dev env — and the natural usage *inverts control*
  (Letta owns the agent loop; our LangGraph pipeline would call into it).
- Their embedded answer, `ai-memory-sdk`, is **experimental (v0.2.0, ~46
  stars) and cloud-only** (requires Letta's hosted service, no self-host) —
  wrong maturity and wrong coupling for the memory layer of a long-running
  show.
- EVOLUTION's argument for Letta — "Spock *should* be the agent deciding what
  to remember" — is addressed differently: Mem0's extraction *is* an LLM
  deciding what to remember, and with a custom prompt it runs **our** curation
  policy; the upcoming ~3-role redesign keeps an Editor/Continuity role that
  curates *using* memory as a tool. Decision-making stays in our stack either
  way.
- **Trigger to revisit:** if Mem0's extraction quality fails the A/B (§5), or
  when multi-day persistent characters need background memory reorganization
  (Letta's sleep-time compute) that Mem0 can't express.

## 3. Design sketch (to finalize after the framework call)

**Keep first-party:** the structured `world_state` ledger + `WorldStateDelta`
merge. It's the props/location ground truth, cheap, already works, and no
framework replaces a domain ledger well. (EVOLUTION's "props ledger that
survives summarization" is *this*, kept.)

**Replace with Mem0:**

- `narrative_memory` (the B3 rewrite) — gone. Post-turn, the beat summary is
  `add`ed to Mem0 (`user_id` = story, `run_id` = session, `metadata.turn`),
  fire-and-forget overlapped with render.
- Context assembly — pre-turn, `search(user_input + short_term_narrative)`
  feeds Tolkien the relevant past instead of the full prose blob;
  Attenborough's callback-spotting read becomes a search too. Recent-N raw
  beats stay in-prompt for local phrasing continuity (B4's window becomes a
  *local-detail* window, no longer the memory system).
- Spock shrinks but stays (this plan does **not** do the role redesign):
  still emits `WorldStateDelta` + the in-scene `context_brief`; stops
  rewriting prose memory. His cost should drop sharply — verify with
  `main.py stats` before/after.

**Multi-session continuity** comes nearly free (`user_id` spans sessions) —
flag it, don't build UX for it yet.

## 4. Out of scope

- The 4→~3 role redesign (separate plan; this plan must not pre-empt it,
  only leave Spock smaller).
- Letta integration (fallback only).
- `data/story.json` changes; substrate work.

## 5. Validation — this is what we kept the scorer for

A/B with the retained judge harness (`docs/findings/judge-methodology.md`):
old-Spock vs Mem0-backed config on the same scripted scenario with fresh
annotations; the **window-memory rubric** (prop persistence, callback
quality, long-horizon coherence) measures exactly the dimensions this swap
targets. Plus `main.py stats` before/after for the cost/latency story.
Acceptance: window-memory scores ≥ old Spock, Spock-agent cost down, no
new warning events.

## 6. Decisions & implementation notes (2026-06-11)

User chose **Mem0** and the **quick-check A/B**. Implemented on
`feat/memory-swap`:

- `src/memory/mem0_store.py` — `StoryMemory` over embedded `AsyncMemory`:
  pinned LLM model, Qdrant + history DB under `logs/memory/`, slugged entity
  ids (Mem0 rejects whitespace in `user_id` — story titles need slugging).
- **Custom-prompt discovery:** in mem0 2.0.5 the `prompt` arg to `add()` is
  NOT a replacement extraction prompt — it lands as a "## Custom Instructions"
  section inside Mem0's own additive-extraction template. A full prompt with
  its own output contract breaks extraction to zero memories; guidance-only
  instructions work. `NARRATIVE_EXTRACTION_INSTRUCTIONS` fixes both spike
  risks (silent loss verified gone on the hop case; no date stamps).
- `config.memory_backend: "prose" | "mem0"` (`configs/mas.mem0.yaml`);
  `_maybe_memory` in the runner; graph gains an optional post-Spock
  `memory_write` node (awaited — lands before next turn's retrieval, so the
  one-turn-delay contract holds).
- Agents: Tolkien queries with `user_input + short_term_narrative`,
  Attenborough with the beat narration; both inject results via a shared
  `{memory_block}` prompt slot (prose mode keeps the old rolling-blob text).
  Spock in mem0 mode emits `MemoryUpdateLite` (delta + brief) via
  `spock.*.mem0.md` prompt variants.
- Bonus: Mem0's extraction call is traced inside the turn's Langfuse span,
  so memory cost/latency shows up in `main.py stats` per turn.

## 7. A/B results (2026-06-11, quick check: 30 turns, N=1 per config)

Sessions `mas_ab_scenario_20260611_112951` (prose) vs
`mas_mem0_ab_scenario_20260611_113601` (mem0); judged with
`data/ab_annotations.json` (4 probe windows); both clean (0 parse/validation
failures, 30/30 turns + 4/4 windows scored).

**Window memory (the swap's target), mean over 4 probes:**

| Dimension | prose | mem0 | Δ |
|---|---|---|---|
| prop_persistence | 2.00 | 2.00 | 0.00 |
| long_horizon_coherence | 2.25 | 2.25 | 0.00 |
| callback_quality | 2.00 | 1.75 | −0.25 |

**Operational (`main.py stats`):** Spock himself got 33% cheaper ($0.231 →
$0.155; output tokens 11.4K → 3.3K — the rewrite is gone) and 2.8× faster
(4.27s → 1.54s mean). But Mem0's extraction calls add $0.247/session — its
additive-extraction template sends ~9K input tokens *per call* — so **total
session cost rose 31%** ($0.576 → $0.754). Wall-clock roughly flat.

**Honest verdict: parity, not superiority — at 30 turns.** The −0.25 is one
integer judge point on one of four probes (single-run noise; the school
benchmark needed N=3 to trust deltas this size). Critically, 30 turns is
*inside prose-Spock's comfort zone* — B3's failure mode (overwriting
earlier-correct details) emerges at turn ~50+, which the quick check cannot
reach by design. The swap's payoff territory (100–1000 turns, multi-session)
is untested.

**Decisions taken on these results:**
- **Merged with `memory_backend` defaulting to `"prose"`** — the swap is
  opt-in infrastructure, zero behavior change to the default path.
- **The long-horizon question stays open.** Re-run this A/B at 100+ turns
  before (or as part of) the agent-redesign plan, where a memory decision
  must be final. Acceptance then: window-memory ≥ prose *at length*.
- **Cost lever identified:** Mem0's per-call extraction context (~9K tokens)
  is the dominant added cost — investigate trimming its additive context
  (last-k window / existing-memories payload) before the long-horizon run.
