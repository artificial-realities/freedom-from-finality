# Finding — Benchmark results (the empirical record)

> **Frozen snapshot from the IKT469 school-project era.** `report/` and `logs/judge/` were never in
> git and are deleted in the cleanup, so **this doc is the only surviving home for these numbers.**
> Methodology behind them: `docs/findings/judge-methodology.md`. Sources cited inline.

**Setup:** 2 configs (MAS = 4-role Tolkien→Spielberg→Attenborough→Spock; Solo = single fully-briefed
GPT-4.1 emitting all four outputs), N=3 paired runs, 180-turn pre-registered scenario, scored by Claude
Sonnet 4.6 on a 0–3 integer scale. Generation GPT-4.1 @ temp 0.7 for both — **the only variable is
prompt shape** *(report/01_introduction.tex; BENCHMARK.md §Experiment Matrix).*

---

## 1. Headline result (N=3, mean ± SD across runs; Δ = MAS − Solo)

*(report/07_results.tex, Table "Pre-registered expectations".)* **All three pre-registered expectations
confirmed.**

| Exp. | Metric | MAS | Solo | Δ | Verdict |
|---|---|---|---|---|---|
| E1 | Internal coherence (setup) | 2.23 ± 0.17 | 2.24 ± 0.09 | −0.01 | Parity — confirmed |
| E1 | Commentary quality (setup) | 2.75 ± 0.08 | 2.81 ± 0.13 | −0.07 | Parity |
| E2 | Prop persistence | 2.09 ± 0.09 | 1.78 ± 0.11 | **+0.32** | MAS advantage |
| E2 | Callback quality | 2.18 ± 0.17 | 2.09 ± 0.06 | +0.09 | MAS advantage |
| E2 | Long-horizon coherence | 2.49 ± 0.07 | 2.20 ± 0.09 | **+0.29** | MAS advantage — confirmed |
| E3 | Rule compliance (adversarial) | 1.07 ± 0.05 | 1.07 ± 0.18 | 0.00 | Parity — confirmed |

The three expectations *(BENCHMARK.md §Aggregation and reporting):* **E1** early-phase parity (light
load, both briefed → indistinguishable); **E2** late-phase MAS advantage on long-horizon coherence;
**E3** adversarial parity (refusing traps is prompt discipline, not coordination).

---

## 2. Window-memory detail, per run (the consistency evidence)

*(report/07_results.tex, Table "Window probe means per run".)* Each cell = mean across all probes in
that session; x̄ = mean across the three runs.

| Dimension | MAS R1 | R2 | R3 | **x̄** | Solo R1 | R2 | R3 | **x̄** |
|---|---|---|---|---|---|---|---|---|
| Prop persistence | 2.00 | 2.07 | 2.21 | **2.09** | 1.73 | 1.67 | 1.93 | **1.78** |
| Callback quality | 2.00 | 2.40 | 2.14 | **2.18** | 2.00 | 2.13 | 2.13 | **2.09** |
| Long-horizon coherence | 2.50 | 2.40 | 2.57 | **2.49** | 2.13 | 2.13 | 2.33 | **2.20** |

**Key point:** MAS is above Solo on long-horizon coherence in **all 3 individual runs**, and on prop
persistence in all 3. The *consistency across runs* — not the gap size — is what makes the result
believable at N=3 *(report/08_discussion.tex §The main result).*

---

## 3. Representative single-run local phase means (illustrative, one run each)

*(logs/judge/mas_test_scenario_20260509_162457.judge.json and solo_..._20260509_160233.judge.json —
one run per config, not the N=3 mean. Shown for the per-phase shape; treat §1 as authoritative.)*
Format: rule_compliance / commentary_on_screen / internal_coherence.

| Phase | MAS (rule / comm / coh) | Solo (rule / comm / coh) |
|---|---|---|
| setup | 1.24 / 2.64 / 2.40 | 1.28 / 2.96 / 2.36 |
| bit_introduction | 1.10 / 2.77 / 2.03 | 1.33 / 2.87 / 2.27 |
| callback_density | 1.06 / 2.71 / 2.06 | 1.20 / 2.66 / 2.29 |
| adversarial | 1.12 / 2.52 / 2.08 | 1.32 / 2.48 / 2.04 |
| compound_callback | 0.73 / 2.63 / 1.83 | 0.80 / 2.43 / 1.73 |
| sleep_and_probes | 1.05 / 2.60 / 2.00 | 0.85 / 2.60 / 1.95 |
| coda | 1.07 / 2.73 / 1.87 | 1.13 / 2.60 / 2.13 |
| **window (prop / callback / lhc)** | 2.00 / 2.00 / 2.50 | 1.73 / 2.00 / 2.13 |

Notes: Solo's `commentary_on_screen` is *higher* in setup (2.96 vs 2.64) and `rule_compliance` runs
slightly higher than MAS in the early phases — consistent with "decomposition buys nothing for local
quality." MAS pulls ahead on `rule_compliance` in `sleep_and_probes` (1.05 vs 0.85). One MAS run scored
only 14 of 15 window probes (`n_window_scored: 14`) — minor data gap.

---

## 4. Interpretation & honest limitations

*(report/08_discussion.tex unless noted.)*

- **Main result:** decomposition consistently beats monolithic on long-horizon memory, and the gap
  appears *in the right place* — absent under light setup load, growing as the story lengthens and the
  single-prompt juggling load rises. Magnitudes are modest (+0.29 / +0.32 on a 0–3 scale); the
  *consistency* is the real signal. **Mechanism:** Spock compresses/organizes memory every turn and
  briefs Tolkien; Solo must do that compression in parallel with beat+shot+commentary in one response,
  and that parallel workload degrades as context grows.
- **Internal-coherence cost (the honest trade-off):** Solo sits ~0.1 *above* MAS on per-turn internal
  coherence after ~turn 130 — coordination overhead, since MAS agents write in isolation and must stay
  mutually consistent *across a forward pass* rather than *within one model call*. The late-game memory
  win appears to outweigh it, but it is a real cost of decomposition.
- **Phantom-prop failure (turn 91):** *both* configs scored poorly on the fake-umbrella refusal —
  neither refused a never-introduced prop. **Not an architectural finding:** both prompts simply lacked
  an explicit "refuse references to unintroduced props" instruction. (The umbrella becomes *real* at
  turn 139, making the same name a memory-miss probe in phase 4 and a memory-hit probe in phase 5.)
- **Rule-compliance baseline is low (~1.0–1.3) even in setup:** LEGO anatomy constraints (no knee
  bends, claw hands, fixed face) are hard for LLMs regardless of prompt shape, so the adversarial signal
  is noisier than ideal — both configs start from a low compliance floor.
- **N=3 is descriptive only** — too small for inferential statistics; reported as a directional finding.
  A larger benchmark (N≥10) would allow proper non-parametric testing *(§Sample size).*
- **Scenario scope:** the white-void story isolates memory drift cleanly but tests *only* prop-and-
  callback persistence — no multi-character coordination, no spatial navigation, no narrative arc.
  Whether the advantage holds in a richer story is an open question *(report/03_scenario.tex §Limitations;
  08_discussion.tex §Scenario scope).*

---

## 5. The scenario (for reproducing / re-grounding future eval)

180 turns, 7 phases *(report/03_scenario.tex; BENCHMARK.md §Scenario):*

| Phase | Turns | Probes | Purpose |
|---|---|---|---|
| Setup | 1–25 | 0 | Seed 8 core props in fixed order (ball t6, cap t8, skateboard t9, dog t13, sunglasses t16, hammer t19, plant t22, harmonica t25) |
| Bit introduction | 26–55 | 0 | Add gags/props; first callback at t54 |
| Callback density | 56–90 | 4 | Heavy callbacks to phase 1–2 props |
| Adversarial | 91–115 | 3 | 12 world-constraint traps (fake umbrella t91; protagonist speech t93/110; single-location t95; knee bend t96; second minifig t98; on-screen text t100; wall t101; bg colour t107; facial expression t109; scale t112; rule break t113) + 5-turn silent run 102–106 |
| Compound callback | 116–145 | 3 | Ambiguous/multi-prop callbacks; umbrella becomes real at t139 |
| Sleep + probes | 146–165 | 3 | Direct memory probes ("where is the hammer?", "first thing you did?", "whose hat?") |
| Coda | 166–180 | 2 | 5-turn silent reverie; long-range callback to turn 1 at t173 |

Story blueprint = a single LEGO minifigure in an infinite white void: one character, one location, no
dialogue, no arc — chosen so **any drift is memory drift, not scene drift**. Seven hard world
constraints (LEGO anatomy, single-character, pure-white background, no on-screen text) double as
adversarial traps in phase 4 *(report/03_scenario.tex §The story blueprint).*
