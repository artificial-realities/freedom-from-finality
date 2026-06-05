# Finding — The four-role architecture

> **Frozen snapshot from the IKT469 school-project era** (data as of 2026-05-25). Describes the
> Tolkien / Spielberg / Attenborough / Spock decomposition as it was *benchmarked*, why it was shaped
> that way, what the benchmark showed about whether the split paid off, and what to carry forward
> when the roles are re-cut (see `docs/NORTH_STAR.md` — the next step collapses these four into ~3
> literature-grounded roles). Sources are cited inline.

---

## 1. The role split as benchmarked

Four async functions ran in a strict serial LangGraph pipeline, one pass per turn, no branching, no
retries, no LLM-directed control flow *(report/04_system_design.tex §Pipeline overview; EVOLUTION.md
§A.1)*:

```
user input → Tolkien → Spielberg → Attenborough → Spock → (history committed)
```

Each agent read a slice of one shared `StoryState` Pydantic object and returned a partial update that
LangGraph merged between nodes *(report/04_system_design.tex §Shared state)*.

### Tolkien — beat writer
- **Owns:** what happens in the next ~5 seconds of story.
- **Writes:** a `Beat` — prose narration (2–4 sentences), a mechanical action line (for the shot
  composer), an outcome summary (for the memory curator), and an updated short-term narrative
  direction.
- **Reads:** current `world_state`, rolling `narrative_memory`, the **last three beats' narration**
  (local phrasing variety), Spock's `context_brief` from the *previous* turn, and `user_input`.
- **Deliberately does NOT read** `visual_style` or `tone_guidelines` — giving him those blurred his
  prose into cinematography/documentary register and eroded the boundary with Spielberg/Attenborough.
  On silent turns he advances the current narrative direction rather than stalling.
  *(report/04_system_design.tex §Tolkien; EVOLUTION.md §A.1 step 3.)*

### Spielberg — shot composer
- **Owns:** translating the beat into an image-to-video prompt.
- **Writes:** a `Shot` — full i2v prompt text, on-screen roster, camera description, motion
  description, and an **end-frame description**. The end-frame description became the starting image
  for the *next* clip — the sole mechanism of cross-clip visual continuity.
- **Reads:** `visual_style` and the **full** character + location blueprints every turn (instructed
  to re-anchor on them each time), plus `world_state` — especially `protagonist_location` and
  `inventory`, so worn/carried props stay in frame even when the beat doesn't re-mention them.
  *(report/04_system_design.tex §Spielberg; EVOLUTION.md §A.1 step 4.)*
- **Dead lever:** `Shot.duration_seconds` existed and Spielberg was prompted to pick 3–15s, but the
  runtime hard-coded 5s (Wan 2.2 only accepted a fixed 5s). Every clip was the same length.
  *(EVOLUTION.md §A.3 B6.)*

### Attenborough — voice-over commentator
- **Owns:** the spoken line over the clip, in the documentary register from `tone_guidelines`.
  **Silence is a first-class choice and the default on most turns.**
- **Writes:** a `Commentary` with a single `voiceover` field.
- **Reads:** the beat, the shot, `narrative_memory` (to spot callbacks / pace against the arc), the
  **last five turns of commentary** (to avoid recycling phrasing), and Tolkien's next-turn direction
  as a look-ahead (longer line if the thread continues, silence/short if a hard cut is coming).
  *(report/04_system_design.tex §Attenborough.)*
- **Note for redesign:** Attenborough only actually consumed camera/motion/end_frame from the Shot,
  not the whole thing — so it never strictly needed Spielberg's full output. *(EVOLUTION.md §A.3 B2.)*

### Spock — memory + context curator
- **Owns:** persistent memory and the next-turn attention hint. **Runs last.**
- **Writes:** three things — a partial `WorldStateDelta` (structured facts, merged into running
  state), a **full replacement** of the rolling `narrative_memory` (compress old, detail recent), and
  a narrow two-line `context_brief` for Tolkien's next turn (which blueprint characters are in scene;
  which recent setups/commitments the next beat should honor).
- **Reads:** all three of the turn's outputs, `world_state`, `narrative_memory`, and recent history.
  *(report/04_system_design.tex §Spock; EVOLUTION.md §A.1 step 7.)*
- **Structural weakness (the reason the next step replaces him):** the **full-rewrite** memory model.
  Spock re-emitted the entire prose memory every turn (~800-token target), which by turn ~50–60 made
  him the costliest agent *and* a coherence risk — he could overwrite earlier-correct details absent
  from the recent window. *(EVOLUTION.md §A.3 B3; `docs/findings/bottlenecks.md` B3 when spun out.)*

---

## 2. How the four coordinated (the load-bearing contracts)

### The one-turn-delayed feedback loop
Spock's `context_brief` from turn *N* is the **first thing Tolkien reads at turn N+1**
*(report/04_system_design.tex §The one-turn-delayed feedback loop)*. This delay is deliberate and is
the single most important coordination mechanism:
- Agents never wait on each other mid-turn; the pipeline keeps a simple forward shape.
- Spock never summarizes a turn in the same forward pass that produced it.
- If Tolkien makes a factual error on turn *N*, Spock catches it and the brief corrects it at *N+1*.
  Over 5-second clips a one-turn correction lag is imperceptible.

`state.context_brief` carrying Spock's turn-(N-1) output **is the entire feedback loop** of the
system *(EVOLUTION.md §A.1 step 1)*. ARCHITECTURE.md flags this as explicitly load-bearing.

### End-frame chaining for visual continuity
Each clip's end-frame description (Spielberg) seeded the next clip. This was the *only* cross-clip
visual continuity mechanism *(EVOLUTION.md §A.4 load-bearing)*. **It dies in the new design** — the
streaming substrate replaces it with chunk/latent conditioning (`docs/NORTH_STAR.md` move 3).

### Role-disciplined state slicing
The discipline was *what each role is deliberately NOT given*, not just what it reads: Tolkien is
denied visual/tone style; Spielberg gets the full blueprint and re-anchors; Attenborough gets history
windows; Spock gets everything but writes narrowly. The slicing kept each agent's workload narrow and
stable so its prompt didn't grow into a juggling problem *(report/04_system_design.tex; BENCHMARK.md
§Research Question — "specialization for free").*

### `WorldStateDelta` partial-update semantics
Spock emitted *deltas*: empty `protagonist_location` = unchanged; `inventory: null` = unchanged; a
list replaces entirely. This kept structured output small without forcing a full world-state rebuild
each turn *(report/04_system_design.tex §Shared state).* A clean pattern worth keeping for the Editor
role even with an external memory framework underneath.

---

## 3. The research-consensus framing (why the split is not a quirk)

Every serious 2026 multi-agent video-generation paper uses a **Director / Cinematographer / Editor**
decomposition: Camera Artist, FilmAgent, ViMax, MovieAgent *(EVOLUTION.md §1.1, §C.7)*. The four-role
split is therefore a recognized research pattern, not an idiosyncratic academic choice. The mapping
*(EVOLUTION.md §C.8)*:

| Artificial Realities role | Film-research analogue |
|---|---|
| Tolkien (beat writer) | Screenwriter **+** Director |
| Spielberg (shot composer) | Cinematographer |
| Attenborough (voice-over) | Narration (often not a separate agent in the literature) |
| Spock (memory + context) | Script supervisor / continuity / Editor |

This mapping is the seed for the planned ~3-role redesign: a Director-Writer (Tolkien + Attenborough's
narration folded in), a Cinematographer (Spielberg, but emitting stream-steering not a discrete shot),
and an Editor-Continuity (Spock, backed by an off-the-shelf memory framework) *(docs/NORTH_STAR.md)*.

Background lineage also relevant: Anthropic's agents-vs-workflows distinction (this is a *workflow*),
Khot et al.'s *decomposed prompting* (subtasks beat monolithic prompts on multi-step reasoning), and
Liu et al.'s *lost-in-the-middle* long-context degradation — which predicts exactly the late-game
forgetting the benchmark targets *(report/02_background.tex.)*

---

## 4. Did the split pay off? (what the benchmark actually showed)

Full numbers in `docs/findings/benchmark-results.md`. The architecture-relevant summary:

**Where decomposition WON — long-horizon memory, late game** *(report/07_results.tex; 08_discussion.tex)*:
- Long-horizon coherence: MAS **2.49 ± 0.07** vs Solo **2.20 ± 0.09** (Δ +0.29), MAS higher on all 3
  paired runs.
- Prop persistence: MAS **2.09 ± 0.09** vs Solo **1.78 ± 0.11** (Δ +0.32).
- The gap appears *in the right place*: absent in setup (light load), grows as the story lengthens and
  the single-prompt juggling load increases. Consistency across runs — not gap size — is what makes it
  believable at N=3.

**Where decomposition LOST or tied:**
- **Late-game internal coherence:** Solo sat ~0.1 *above* MAS after ~turn 130 *(07_results.tex;
  08_discussion.tex §Internal coherence cost)*. The hypothesized cause is **coordination overhead** —
  MAS agents write in isolation, so Spielberg's shot and Spock's delta must stay consistent with
  Tolkien's narration *across a forward pass* rather than *within one model call*. Solo gets that
  consistency for free. This is the expected, honest cost of decomposition.
- **Commentary-on-screen (setup) and adversarial rule-compliance:** roughly tied / slightly favored
  Solo — decomposition buys nothing when the task is local quality or refusing a constraint-violating
  request *(07_results.tex E1/E3.)*

**The core mechanism (visible in the design):** Spock compresses/organizes past events every turn and
his brief points Tolkien at in-scene characters and pending setups. Solo must do that compression
*in parallel* with generating beat/shot/commentary inside one structured response; as context grows,
that parallel workload degrades. Decomposition keeps each workload small and stable
*(08_discussion.tex §The main result).*

---

## 5. Transfer notes for the redesign

**Keep load-bearing — re-cut the roles however, but preserve these contracts** *(EVOLUTION.md §A.4)*:
- The **one-turn-delayed feedback loop** (Editor's context brief → Director next turn). This is the
  coordination backbone; don't collapse it into same-pass summarization.
- **Narration as the shared prose source** between the visual role and the commentary — the beat's
  narration was the common ground both downstream roles read.
- The boundary between **"what happens" (Director)** and **"how it's shot" (Cinematographer)**.
- The boundary between **current truth (`world_state`)** and **rolling memory (`narrative_memory`)** —
  keep these as distinct subsystems even when an external memory framework backs them.
- **Deterministic Pydantic state, separate from narration prose** *(CLAUDE.md hard constraint).*

**Incidental — free to change:**
- The specific role **names** (Tolkien/Spielberg/Attenborough/Spock).
- The **strict serial graph topology** — Spielberg + Attenborough depend only on Tolkien's output and
  could run concurrently; the strict chain was unforced *(EVOLUTION.md §A.3 B2).* (But note B2 is
  likely mooted by the redesign itself.)
- **LangGraph specifically** — the MAS graph was ~50 lines of straight-line edges; any async + typed-
  state shim works *(EVOLUTION.md §A.4).*
- Attenborough as a *separate LLM agent* — narration can fold into the Director role, with TTS as a
  non-agent service *(docs/NORTH_STAR.md).*

**Things that DIE in the redesign (do not preserve):**
- End-frame-PNG chaining → replaced by streaming chunk conditioning.
- Spock's full-rewrite `narrative_memory` → replaced by an off-the-shelf memory framework.
- The fixed-5s clip identity ("one turn = one beat = one 5s clip") → the streaming clock decouples
  story turns from clip count *(EVOLUTION.md §A.6).*
