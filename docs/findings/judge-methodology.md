# Finding — Judge / evaluation methodology

> **Frozen snapshot from the IKT469 school-project era** (data as of 2026-05-25). The judge *code*
> survives (`src/eval/judge.py`, `src/eval/langfuse_fetch.py`, `src/llm/anthropic_backend.py`,
> `src/prompts/judge.*.md`) as reusable A/B-testing infrastructure for future evolution steps — but
> the *rationale* below lived in the now-deleted `docs/BENCHMARK.md`, so it is preserved here. Any
> future eval plan should re-point the rubric at product dimensions and supply fresh annotations.
> Sources cited inline.

---

## 1. The cross-family judge principle (the methodological core)

Generation was **GPT-4.1 (OpenAI)**; the judge was **Claude Sonnet 4.6 (Anthropic, `claude-sonnet-4-6`)**.
Using a *different model family* to judge closes the **self-preference loophole** — the known LLM-as-
judge bias (Zheng et al.) where a model rates same-family outputs more favorably *(BENCHMARK.md §The
judge model; report/02_background.tex §LLM-as-judge).* This is the single most important credibility
move and should be preserved in any future eval: **judge with a different family than you generate
with.**

The judge ran **post-hoc** against finished Langfuse sessions (`python main.py judge <session_id>`),
scoring on-disk transcripts and uploading one `Score` per `(turn, dimension)` to Langfuse, name-
prefixed `local.*` / `window.*` so dashboards/exports filter by layer *(BENCHMARK.md §The judge model).*

Efficiency move worth keeping: the judge system prompt was sent with **`cache_control: ephemeral`** so
the rubric (reused across 180 per-turn calls) was paid once, not per turn *(BENCHMARK.md §The judge
model).*

---

## 2. The two-layer rubric (integer 0–3 scale, dimensions independent)

Scale across both layers: **3** excellent, **2** solid/minor wobble, **1** visible flaw, **0** failure.
"Apply each dimension independently; do not let one drag another down." *(src/prompts/judge.local.system.md.)*

### Layer 1 — per-turn local rubric (every turn; judge sees ONLY this turn)
The judge receives the turn's normalised `{beat, shot, commentary, memory}` payload (identical shape
for solo and MAS), the user input, and a small annotation block (phase, prop introduced, callback
target, trap kind). **It does not see prior turns** — local quality is judged locally
*(BENCHMARK.md §Layer 1; judge.local.system.md).* Three dimensions:
- **`local.rule_compliance`** — one broad dimension over the whole story-rule set: LEGO anatomy (no
  knee bends, claw hands, fixed printed face), no protagonist speech, no on-screen text, single
  character, locked pure-white void, visual anchor on the minifig. **Silent compliance with an
  adversarial trap scores 0; a clean refuse/redirect/improvise scores 3.**
- **`local.commentary_on_screen`** — voiceover lands on visible action in the documentary register.
  **Empty voiceover is a first-class 3** when silence is appropriate.
- **`local.internal_coherence`** — the four artifacts mutually agree *this turn* (shot matches beat
  action, end-frame follows beat outcome, delta reflects the change, brief makes sense for next turn).

Comments: one factual sentence; for any score ≤2 the comment must name the specific artifact+field
that drove it *(judge.local.system.md §Output format).*

### Layer 2 — per-window memory rubric (15 probes; judge sees a window transcript)
At each of 15 pre-registered probe turns the judge sees the transcript from `window_start` to
`score_at_turn` plus an annotation block stating what the probe tests and which earlier turns are the
referents *(BENCHMARK.md §Layer 2; src/prompts/judge.window.system.md).* Three dimensions:
- **`window.prop_persistence`** — earlier props still retrievable; consumed props stay gone; worn props
  stay on. Fake-callback traps: refusal/confusion is correct, silent compliance scores 0.
- **`window.callback_quality`** — references to earlier props/bits are recognized as callbacks and
  built on, not restarted. For *ambiguous* callbacks, committing to any recognizable prior referent
  scores 3 (no single right answer).
- **`window.long_horizon_coherence`** — reading the window cold, does late content behave as if it has
  read the early content? **The central dimension for the research question.**

Comments must cite specific turn numbers for any score ≤2 *(judge.window.system.md §Output format).*

---

## 3. The pre-registered scenario as ground truth

180 user commands in 7 phases (Setup / Bit-introduction / Callback-density / Adversarial / Compound-
callback / Sleep+probes / Coda), with an **annotations file** recording: which prop was introduced at
which turn, which turns are explicit callbacks (and to which earlier turns), which turns are
adversarial traps (and which rule they target), and **15 probe windows** (each = a score-at turn, a
prior-turn range to include, and a one-sentence description of what it tests)
*(report/03_scenario.tex; BENCHMARK.md §Scenario).* The judge receives the annotation alongside the
transcript so its scoring is **grounded against the scenario design, not its own intuitions** — the
mechanism that made the rubric reproducible. (Full scenario detail: `docs/findings/benchmark-results.md`.)

---

## 4. Credibility moves & honest limits

- **Paired N=3 runs.** Multiple runs is the biggest lift over a one-shot comparison — LLM stochasticity
  dominates a single shot; N=3 gives within-config spread and 3 paired observations per phase
  *(BENCHMARK.md §Total).*
- **Descriptive, NOT inferential.** No p-values. Paired Wilcoxon at N=3 cannot honestly reach p<0.05;
  the project reports phase means + run-to-run range + counts of paired observations favoring each
  config ("MAS higher on X in 5 of 6 late-phase observations"). Positioned as a *descriptive comparison
  under a fixed cross-family rubric*, not a hypothesis test *(BENCHMARK.md §Aggregation and reporting;
  report/08_discussion.tex §Sample size).*
- **No human calibration.** The judge was never calibrated against human raters — explicitly noted as a
  limitation *(BENCHMARK.md §Aggregation and reporting).*
- **Rubric-driven, not free-form.** Identical normalised payload shape for both configs (`_resolve_outputs`
  in judge.py) means any new config scores through the same rubric with no judge changes *(EVOLUTION.md
  §A.5).* This is what makes the harness reusable for future A/B tests.

---

## 5. Cost envelope (N=3, school-era prices — re-validate)

*(BENCHMARK.md §Cost envelope.)*
- **Generation** (GPT-4.1, both configs × 3 runs × 180 turns): Solo ≈ 540 calls, MAS ≈ 2,160 calls →
  **≈ $24**.
- **Judging** (Claude Sonnet 4.6): per-turn local rubric 1,080 calls (~3K in + ~250 out, cached) ≈ **$11**;
  per-window rubric 90 calls (~30K in + ~400 out) ≈ **$5**.
- **Total ≈ $35–50** per full cycle. End-to-end smoke validation +~$15. Re-judging an existing run after
  rubric tweaks ≈ $15 (generation is cached on disk + Langfuse).

MAS cost ≈ 4× solo per turn before token differences — the system bills proportional to specialization,
which caps the experiment ceiling *(EVOLUTION.md §A.3 B8).*
