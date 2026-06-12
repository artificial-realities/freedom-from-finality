# Plan 03 — Agent redesign: 4 roles → ~3 literature-grounded roles

> **Status: decided 2026-06-12 — candidate B (clock-split) chosen by the user. Implementing.**
> North-star move 2. Self-contained: includes the literature research, the constraints from our
> own findings, three candidate role-cuts with trade-offs, and the implementation spec for B.
> The deciding 100+-turn memory A/B (plan 02 rider) runs on the chosen design.

## 1. Where we are

Four roles run serially per turn: Tolkien (beat) → Spielberg (shot) → Attenborough (voiceover) →
Spock (world ledger + brief; episodic memory now optionally in Mem0 per plan 02). Baseline
telemetry (5-turn smoke, `main.py stats`): ~$0.017/turn, ~12s/turn serial LLM latency.

The north star (move 2) proposes collapsing to ~3 roles — Director-Writer / Cinematographer /
Editor-Continuity, narration folded into the Director — and says the shape should be co-designed
with the streaming substrate (move 3). This plan does the design work and implements the cut on
the **current clip substrate**, so the redesign is judgeable now; the per-chunk output schema gets
its final streaming shape in the substrate plan.

## 2. Literature findings (researched 2026-06-12)

### 2.1 The offline film-agent stack — role-cuts and evidence

- **FilmAgent** ([arXiv 2501.12909](https://arxiv.org/abs/2501.12909)) — Director / Screenwriter /
  Actors / Cinematographer×2, coordinated by Critique–Correct–Verify loops and a Debate–Judge
  protocol (max 3 rounds). Ablations: staging alone helps modestly (CoT 2.63 → Solo 3.04 avg);
  **collaboration is the big win** (Group 3.98), concentrated in Plot (+1.66) and Camera (+1.46).
  Multi-agent GPT-4o beat single-agent o1. Camera choice was the *weakest* single-agent output and
  the most improved by a second opinion (90% preference after debate) — the strongest published
  argument for keeping the shot role distinct from the writer.
- **MovieAgent** ([arXiv 2503.07314](https://arxiv.org/abs/2503.07314)) — despite four-role
  marketing, formalizes exactly **three planning agents**: Director (sub-scripts) → Scene Plan →
  Shot Plan, threaded through a character bank. Ablations: hierarchy +0.17 avg over a single
  planner; per-role internal CoT +0.20 coherence.
- **ViMax** ([HKUDS repo](https://github.com/HKUDS/ViMax), paper under review) — 13 agent modules,
  batch rendering, but its **planning layer is already autoregressive**: StoryboardArtist plans
  shot-by-shot with an `is_last` flag. Its typed `ShotDescription` (visual/motion/audio desc,
  variation type, visible-character indices) is a ready-made per-chunk conditioning schema. Its
  consistency machinery is a *visual* memory: character-portrait registry + per-shot LLM selection
  of reference images.
- **Camera Artist** ([arXiv 2604.09195](https://arxiv.org/abs/2604.09195)) — three agents:
  Director / Cinematography Shot Agent / Video-Gen Agent. **Recursive Shot Generation** = each
  shot conditions on (previous shot, scene, global script) with typed start/mid/end termination —
  the offline ancestor of a per-chunk director loop. **Cinematic Language Injection** = a small
  fine-tuned rewriter (Qwen3-4B + LoRA on 580 ShotBench pairs) that turns plain shot descriptions
  into professional cinematic language. Ablations: removing RSG hurts script consistency
  (3.90→3.55); removing CLI hurts camera-movement consistency (3.55→2.83) — i.e. "what happens"
  vs "how it's filmed" are separable, and the latter can be a cheap rewriter, not a full agent.

### 2.2 The streaming frontier — and the gap

**No published system pairs an LLM director loop with a real-time autoregressive video model under
live user steering.** The field converged from two sides without meeting:

- Real-time interactive models where steering is a raw prompt switch, no agent: **LongLive**
  ([arXiv 2509.22622](https://arxiv.org/abs/2509.22622), KV-recache at prompt boundaries, 20.7 FPS
  on one H100), **ShotStream** ([arXiv 2603.25746](https://arxiv.org/abs/2603.25746), next-shot
  generation with a dual visual cache, 16 FPS, explicitly no LLM director), **Genie 3**
  (promptable world events, no narrative).
- Offline multi-agent director stacks: everything in §2.1 plus MUSE, InfinityStory, DirectorLLM.

Three transferable streaming results:

- **StreamChar** ([arXiv 2605.25659](https://arxiv.org/html/2605.25659)): a causal LLM
  orchestrator emitting frame-aligned conditioning per ~1.4s chunk hits real-time factor ≈ 1 on one
  H100 — an LLM in the per-chunk loop fits the budget **iff its output is a small structured
  conditioning signal, not free-form planning**.
- **IAMFlow** ([arXiv 2605.18733](https://arxiv.org/html/2605.18733)): the only published LLM in a
  per-chunk loop — and its job is **memory curation, not narrative** (entity-ID registry, frame
  retrieval, **async background VLM verification** that patches the registry without blocking
  generation). Their core lesson: symbolic entity memory beats token-buffer/prose memory for
  streaming identity.
- Latency arithmetic ([Huang, "Towards Video World Models"](https://www.xunhuang.me/blogs/world_model.html)):
  between-chunk steering at ~5s chunks sits in the ~1s interactivity class — generous; a 2-call
  serial LLM path fits comfortably.

**Implication:** our watch-and-steer loop is the unpublished combination. The role-cut should be
designed so the per-chunk critical path is short and structured (StreamChar lesson), with heavier
curation pushed off the critical path (IAMFlow lesson).

## 3. Constraints from our own findings (`docs/findings/four-role-architecture.md`)

Keep load-bearing, whatever the cut:

1. **One-turn-delayed feedback loop** — Editor's brief from turn N is read by the Director at N+1.
   Note: this contract *already tolerates async* — the editor may finish any time before the next
   Director call. Async-ifying the Editor is contract-preserving by construction.
2. **Narration as the shared prose source** for the visual and commentary roles.
3. The **"what happens" / "how it's shot" boundary** (also Camera Artist's RSG/CLI ablation).
4. **Current truth (`world_state`) vs rolling memory** as distinct subsystems (Mem0 now backs the
   latter).
5. **Deterministic Pydantic state** separate from prose.

Two findings that *push against* the naive north-star fold:

- **Register blur:** Tolkien was deliberately denied `tone_guidelines`/`visual_style` because
  having them eroded his prose into documentary/cinematography register. Folding Attenborough's
  voiceover into the Director's output puts the tone guidelines back into the writer's prompt.
- **The brief is the core mechanism:** the benchmark's long-horizon win traced to Spock's
  compress-and-point work. Demoting continuity to a non-agent service risks the one thing the
  decomposition measurably bought.

One streaming reality that reframes Attenborough: voiceover audio already spans multiple clips and
is gated by a silence floor (`min_pause_seconds`) — the narrator is *already* on a different clock
than the chunk loop. The fixed "one turn = beat + shot + voiceover + memory" identity is an
artifact of the clip substrate.

## 4. Candidate role-cuts

All candidates share: Cinematographer output reshaped for streaming later (next-prompt + switch
signal replaces end-frame chaining — substrate plan); Mem0 as the episodic layer; TTS as a
non-agent service; judge harness A/B as the acceptance test.

### A — Fold-in three (the literal north-star shape)

**Director-Writer** (beat + voiceover fields in one output) → **Cinematographer** →
**Editor** (ledger delta + brief; Mem0 write).

- - Literature-standard (MovieAgent and Camera Artist are both 3-role). Fewest prompts to
  maintain. One fewer LLM call per turn than today.
- − Collides head-on with the register-blur finding: the writer's prompt now carries tone
  guidelines. Voiceover stays chained to the chunk clock even though audio spans chunks — the
  Director decides "speak or not" every turn forever.

### B — Clock-split (recommended)

Re-cut by **clock**, not by merging prompts. Per-chunk critical path: **Director** (beat) →
**Cinematographer** (chunk spec). Off the critical path: **Editor** runs *async post-chunk*
(ledger delta + brief + Mem0 write — lands any time before the next Director call, which the
one-turn-delay contract already permits); **Narrator** moves onto the *audio clock* — invoked only
when the silence floor opens, reading the beats accumulated since it last spoke.

- - Preserves all five contracts *including* register separation (4 prompts survive ~unchanged).
  Per-chunk serial path drops from 4 LLM calls to 2 — the latency cut lands exactly where the
  streaming substrate needs it (StreamChar's lesson), and the async Editor is IAMFlow's published
  pattern. Narrator-on-the-audio-clock formalizes machinery the live loop already has
  (`min_pause_seconds` gating, audio spanning clips). Roles stop being "stages in a pipeline" and
  become "loops on their own clocks" — which is the honest shape of a streaming system.
- − Nominally still four roles (reorganized, not reduced) — it satisfies "~3" only as "3 roles in
  the turn structure, narrator off-loop". Orchestration gets real: two clocks + one async join
  (Editor must complete before next Director read; on the clip substrate that join is trivial,
  in streaming it needs a timeout/skip rule).

### C — Lean two

**Director-Writer** (beat + voiceover) → **Cinematographer**. Continuity becomes non-agent
services: Mem0 extraction for episodic memory + one cheap structured-extraction call for the world
ledger; **no curated brief** — the Director reads retrieved memories + ledger directly.

- - Cheapest and fastest; maximal simplification; bets fully on Mem0 retrieval.
- − Kills the curated `context_brief` — the benchmark's core long-horizon mechanism — before the
  long-horizon A/B has shown retrieval covers it. Double register risk. High risk; only
  rational *after* an A/B shows the brief is redundant.

### Recommendation: **B**, with C's question answered empirically

B preserves every validated contract, takes the streaming reframe seriously (clocks, not stages),
and is the only cut that *shortens the critical path without deleting evidence-backed machinery*.
The long-horizon A/B (plan 02 rider) then tests prose-Spock vs Mem0 *on the B design*; if Mem0 +
retrieval makes the brief measurably redundant, C becomes a cheap follow-up cut (delete the Editor
prompt, keep the extraction call). A is strictly worse than B on our own evidence: it buys "one
fewer call" at the cost of the register finding, while B buys *two* fewer calls on the critical
path and keeps it.

## 5. Implementation spec (candidate B)

Naming: **keep** the persona names (Tolkien/Spielberg/Attenborough/Spock) for files, agents and
prompts — the findings doc calls names incidental, and B preserves all four prompts ~unchanged.
The role mapping (Director/Cinematographer/Narrator/Editor) is documented in the engine docstring.

### 5.1 Turn engine replaces the LangGraph pipeline

`src/graph/mas_graph.py` is rewritten as a **`TurnEngine`** class (factory `build_mas_graph` and
the `graph: "mas_graph"` config key survive for compatibility). LangGraph is dropped from the
runtime path — the findings doc already called it incidental, and removing it lets the engine
mutate the live `StoryState` in place (which also kills the runner's "re-arm pacing flags after
ainvoke rebuilt state" hacks). `engine.ainvoke(state)` keeps the call-site signature.

Per turn:

1. **Join** — await the previous turn's Editor task (if any) and apply its update
   (`world_state`, `context_brief`, `narrative_memory` in prose mode). This is the one-turn-delay
   contract made explicit: the brief must land before the Director reads.
2. **Critical path** — Tolkien (beat) → Spielberg (shot). Two serial LLM calls.
3. **Narrator clock** — the engine (not the agent) decides whether the silence floor is open:
   gate open iff no voiceover yet, or (`audio_seconds_owed ≈ 0` and
   `silence_seconds ≥ min_pause_seconds`). Gate open → call Attenborough; closed → set silent
   `Commentary` without an LLM call. In audio modes (`pacing_managed`) the runner keeps doing
   measured bookkeeping; in non-audio modes the engine maintains `silence_seconds` itself using
   `i2v_duration` as story-time per turn — the narrator is on the same clock in **all** modes now
   (previously he ran every turn outside the live loop).
4. **Spawn Editor** — deep-snapshot the state, spawn an asyncio task running Spock (+ the Mem0
   `add_turn` write in mem0 mode). The task inherits the turn's Langfuse span via contextvars, so
   its generation attributes to the turn it describes. Exceptions are logged, never fatal —
   identical to today's "Spock parse failure skips the update".

`engine.drain(state)` (called in every runner `finally`) joins the last Editor task so the final
world state is complete and the last Mem0 write lands before exit.

### 5.2 Attenborough changes

- Internal pacing gates move out of the agent (the engine owns the clock); the agent is only
  invoked when the room is open.
- New input: **beats since your last line** — narrations accumulated since the last non-empty
  voiceover (capped), so a line can pay off a thread that built across several silent clips.
- System prompt reframed: "you are invoked when the room opens" replaces "most turns you should
  return empty" (mechanical silence is now enforced by the clock; his choice is still
  silence / short / long).

### 5.3 Knock-on changes

- `_commit_history` keeps requiring beat+shot+commentary — the engine always sets a `Commentary`
  (possibly silent), so silent turns commit normally.
- The per-turn story log now shows the brief/ledger as of the *previous* turn (the Editor result
  lands at the next join). This is the honest async semantics; the judge reads narration/voiceover,
  not the ledger dump, so A/B comparability is unaffected.
- `requirements.txt`: drop `langgraph`.
- Docs: CLAUDE.md tagline, ARCHITECTURE.md, AGENT_FLOW.md, findings/four-role-architecture.md
  transfer notes (strict serial topology is dead; contracts preserved).

### 5.4 Validation

1. Smoke run (5-turn scenario, prose backend, no video): verify join ordering, narrator cadence,
   editor traces landing on the right turns (`main.py stats`).
2. Judge A/B on the 30-turn `data/ab_scenario.json` with `data/ab_annotations.json`: new-B (prose)
   vs the old-4-role prose arm from plan 02 §7 (same scenario, same judge, same annotations —
   cross-session comparable). Expect parity on memory rubrics, different commentary cadence
   (intended), and a per-turn cost/latency drop on the critical path.
3. The 100+-turn prose-vs-Mem0 A/B (plan 02 rider) follows as its own chunk on the B design.

## 6. Results (2026-06-12, implemented and validated)

Implemented exactly per §5. Smoke runs clean on both backends (prose
`mas_demo_scenario_20260612_103550`, mem0 `mas_mem0_demo_scenario_20260612_103809` — the final
Mem0 write landing after the loop confirms `drain()`).

**Judge A/B** — 30-turn `ab_scenario`, same judge (claude-sonnet-4-6), same annotations, prose
backend both arms. Old arm: `mas_ab_scenario_20260611_112951` (plan 02 §7). New arm:
`mas_ab_scenario_20260612_103939`. Judge re-run on the new session reproduced identical scores,
so the judge itself is stable; generation-side N=1 caveat still applies.

| Dimension | old serial 4-role | new clock-split | Δ |
|---|---|---|---|
| prop_persistence (window) | 2.00 | 2.75 | **+0.75** |
| callback_quality (window) | 2.00 | 2.75 | **+0.75** |
| long_horizon_coherence (window) | 2.25 | 3.00 | **+0.75** |
| internal_coherence (probe-phase local) | 1.7 | 2.0 | +0.3 |
| rule_compliance / commentary_on_screen | 1.0 / 2.7 | 1.0 / 2.8 | tie |

Better-or-equal on everything. The callback gain has a plausible mechanism (the narrator now
reads the beats accumulated since his last line and speaks only when invited — 15 calls instead
of 30, every line informed by a quiet stretch of story); the prop/coherence gains may be partly
single-run variance — treat the direction, not the magnitude, as the finding.

**Operational** (`main.py stats`): session cost $0.576 → $0.549 (−5%; Attenborough 15/30 calls,
$0.045). Critical path per turn is now Tolkien + Spielberg ≈ 5.7s mean vs ~12s for the old 4-call
chain; Spock's ~5.1s overlaps rendering/user-wait (in back-to-back scenario mode it still mostly
serializes at the join — the win is real in live mode, where it vanishes under the ~30s render).

**Residue cleaned with this plan:** LangGraph retired (plus never-imported `langchain-*` and
`pandas` pulled from requirements); runner "re-arm pacing after ainvoke rebuilt state" hacks
deleted (the engine mutates state in place).
