# Plan 04 — Streaming substrate: hands-on LTXV spike

> Visual write-up (frame comparisons for §3.3/§3.4): https://claude.ai/code/artifact/6954c1cd-0773-4ba9-b7fc-0807fa58182a

> North-star move 3. Self-contained. This plan is the hands-on GPU spike the north star's
> substrate reality check (`docs/NORTH_STAR.md`) calls for before committing to a runner rewrite:
> chunk size vs. coherence vs. latency, and whether the marketing-vs-repo gap flagged in June
> (and re-confirmed in the 2026-09-03 re-validation) is as wide as it looks.

## 1. Where we are

Current substrate: Wan 2.2 (`dashscope-i2v-flash` via Alibaba DashScope) generating ~5s batch
clips, chained by feeding each clip's last frame as a PNG into the next clip's i2v conditioning.
~30s wall-clock per clip on the API. This is the thing move 3 replaces.

`docs/NORTH_STAR.md`'s substrate reality section (validated 2026-06-05, re-validated 2026-09-03)
identified **LTXV (the autoregressive/distilled line of LTX-Video)** as the self-hostable
candidate, with an explicit caveat both times: Lightricks' marketing ("60-second real-time
streaming," "interactive livestreaming") overstates what the public repo actually exposes — no
streaming inference API, chunk-extension is batch-orchestrated. Both prior validations were
literature/marketing research, not hands-on. This plan is that hands-on check.

## 2. Method

Rented a Vast.ai A100 SXM4 (40GB, $0.668/hr) for the spike. `Lightricks/LTX-Video` cloned, the
`ltxv-2b-0.9.8-distilled` config (bfloat16, no extra Q8-kernel dependency) run via `inference.py`.

Plan:
1. Generate an initial i2v clip from `data/legoman.png` (the project's own reference image) with
   a prompt matching `data/story.json`'s visual style — timed, cold (includes first-run model
   download) and warm.
2. Extend the clip via `inference.py`'s `conditioning_media_paths` pointed at the *previous
   video segment* (not just its end frame) — this is the real mechanism, and the thing to
   evaluate against end-frame-PNG chaining: does conditioning on actual latents beat conditioning
   on a single PNG for visual continuity.
3. Inject a **new prompt** on the second extension — the actual test of "steer between chunks":
   does the extension obey the new instruction while staying visually continuous with what came
   before, the same job Spielberg's shot spec does today.
4. Repeat for a short chain (4-5 chunks) and log wall-clock per chunk.
5. Watch the chained output for coherence: does the minifigure's identity/proportions drift,
   does the background stay pure white, does stop-motion character survive re-conditioning.

## 3. Results (2026-09-03, hands-on, Vast.ai A100 SXM4 40GB, $0.668/hr)

Total spend: **$0.22** (~19 minutes billed). `ltxv-2b-0.9.8-distilled` (bfloat16), not the 13b —
picked for iteration speed; §5 flags the 13b re-run as the natural follow-up.

**Setup note:** the repo's loose `diffusers>=0.28.2` pin resolved to 0.39.0, which is incompatible
with torch 2.4.0's custom-op registration (`infer_schema` rejects the flash-attn-3 op signature).
Pinned to `diffusers==0.31.0`. Anyone re-running this needs the same pin or a torch upgrade.

### 3.1 The marketing-vs-repo gap — confirmed, precisely

No streaming/chunk API exists anywhere in `Lightricks/LTX-Video` — grepped the README for
"stream", "autoregress", "chunk": zero substantive hits beyond "real-time" meaning *fast batch
inference*, not a live process. The repo's own README now says **LTX-2 is the primary home for
LTX development**; this repo is semi-legacy. "Video extension" is a CLI flag
(`--conditioning_media_paths` pointed at the previous output) that reruns the *entire* pipeline
from scratch, conditioned on the prior segment. There is no persistent process, no partial-output
streaming, no session state between calls. The north star's June caveat — confirmed again in the
Sept re-validation from marketing/docs alone — holds exactly, now from direct measurement:
**"chunk-extension is batch-orchestrated," full stop.**

### 3.2 Latency: process-reload overhead dominates, not compute

| Run | Wall clock | What's included |
|---|---|---|
| Cold (first ever) | 106.6s | weight download (~30s) + prompt-enhancer model download + load + sample |
| Warm, 41 frames (1.4s clip) | 44.5s | fresh process, cached weights, load + sample |
| Warm, 81 frames (2.7s clip, extension) | 54.4s / 51.9s | fresh process, cached weights, load + sample |

Actual diffusion sampling — read off the progress bars — is **under 2 seconds** (7 first-pass
steps at ~17-20it/s + 3 second-pass steps at ~4-6it/s ≈ 1-1.5s combined). Everything else in the
44-54s is `inference.py` rebuilding the pipeline from disk on every invocation: checkpoint shards,
text encoder, and — unprompted — a Florence-2 image-captioning model it loads to process the
conditioning image, every single call.

**This is the load-bearing number for the whole plan.** The repo's real-time claim is true of the
*sampler*, not the *CLI*. A per-chunk director loop calling `inference.py` as a subprocess per
turn would pay ~50s/chunk — worse than the current Wan2.2 substrate this plan exists to replace.
Getting anywhere near the "~1s first-frame" figure in `docs/NORTH_STAR.md` requires a **resident
inference server** holding the pipeline in VRAM between calls — not exposed by this repo, would
need to be built. That's a real, scoped engineering task, not a research question; §5 sizes it.

### 3.3 Identity/framing fidelity — a real problem, undirected

First clip: `legoman.png` → i2v, prompt asked for a static product-shot wave, explicitly "no
camera flourish." Within the clip's own 41 frames (1.4s @ 30fps) the framing drifted from
full-body to a tight face close-up, and the model **invented facial details absent from the
source** — hair-line, eyebrows — the actual LEGO minifigure has neither (just a printed face on a
smooth head). By frame ~20 of 41 (0.7s in) the drift is already complete and stays there.

This is exactly the failure mode `docs/findings/` and the story blueprint (`data/story.json`)
care most about — visual continuity of a single fixed character with no protagonist redesign — and
at 2b-distilled scale, undirected, it fails inside of one clip, before any multi-chunk chaining is
even involved. Not fatal (see §3.4), but real: the smaller distilled model does not hold this
project's specific brief on its own.

### 3.4 Steering between chunks — works, but conditioning_strength is the dial

Extension test: fed the first clip's *video* (not an end-frame PNG) back in as conditioning with a
new prompt ("crouches down and picks up a small red brick from the floor, inspects it").

- **Continuity across the seam:** clean — no jarring cut, the (drifted) identity from clip 1
  carries through frame-exact into the extension. Video-latent conditioning genuinely beats
  end-frame-PNG chaining here, as the north star hypothesized.
- **At default `conditioning_strength=1.0`:** the new prompt was almost entirely ignored — the
  figure stayed in the clip-1 wave pose through the whole 81-frame extension, no crouch, no brick,
  arms lowered slightly by the end but no new action.
- **At `conditioning_strength=0.5`:** the new prompt took — the figure genuinely picks up a red
  brick and holds it up to inspect it (not quite "crouches," but the core action landed), while
  the same drifted identity and framing carried through cleanly.

**This is the finding that matters most for viability.** Steering isn't broken — it's gated behind
a strength parameter that trades continuity for obedience, and 1.0 (the value a naive integration
would use) silently fails toward *ignoring the user*. A production per-chunk loop needs to treat
`conditioning_strength` as a real per-turn decision — plausibly Spielberg's job, choosing a lower
value on turns with a strong new user instruction and a higher one on quiet continuation turns —
not a fixed constant.

## 4. Verdict

**Proceed, with the plan reshaped by what actually got measured, not what the marketing promised.**
LTXV is real and self-hostable, but the substrate isn't "streaming" in any literal sense — it's a
better *batch extension* primitive than Wan2.2's end-frame-PNG chaining (richer conditioning,
tunable steer-vs-continuity via `conditioning_strength`), wrapped in a CLI whose per-invocation
overhead currently makes it *slower* than the substrate it would replace. The win is real but
needs its own engineering, not a config swap:

1. **A resident inference server is required, not optional.** `inference.py`'s ~50s/call is
   almost entirely avoidable reload cost; a service holding the pipeline in VRAM should get
   per-chunk latency close to the ~1-2s sampling time actually observed. Scoping/building this is
   the next concrete chunk of work — bigger than a script, smaller than a full plan.
   *(Resolved — §5: 1.3s steady-state per chunk, confirmed. §5 also surfaced a worse identity
   problem than §3.3's, still open.)*
2. **Re-run the identity-fidelity test on `ltxv-13b-0.9.8-distilled`.** The 2b model's drift into
   invented facial detail may not survive at 13b; this determines whether the LEGO-in-white-void
   brief is viable on this substrate at all, or needs art-directed negative prompting / a control
   LoRA.
3. **`conditioning_strength` needs to become a per-turn model decision**, not a constant — likely
   folded into Spielberg's shot spec once the agent side of this integration is designed.

## 5. Resident-server benchmark (2026-09-03, follow-up #1 — done)

Built the fix §4 called for: a script that constructs the LTXV pipeline **once**
(`create_ltx_video_pipeline`, wrapped in `LTXMultiScalePipeline`) and then runs a simulated 8-turn
play session against the resident object — no process restart, no reload, between turns. New A100
PCIE instance ($0.602/hr), separate from §3's; total spend this round **$0.17**.

**Methodology change from §3.4, and why it matters.** §3's extension test conditioned on the
*entire* prior video and asked for a growing `num_frames` (41 → 81) each call — but
`prepare_conditioning`'s `trim_conditioning_sequence` confirms `num_frames` is the pipeline's
*total target output length*, fully re-diffused end to end every call. Grown unbounded across a
long session, that's not streaming, it's quadratic cost. The architecturally sane scheme — the one
this benchmark actually tests — is a **fixed-size sliding window**: condition each turn on only the
last 9 frames of the previous chunk, always request a fixed 41-frame output. That keeps per-turn
compute flat regardless of session length, matching what a real chunk-streaming service needs.

### 5.1 Latency: resolved, and by a wide margin

| | Wall clock |
|---|---|
| Pipeline load (one-time, paid once at service startup) | 15.3s |
| Turn 0 (i2v from the reference image) | 2.77s |
| Turns 1–7 (steady-state, sliding-window extension) | mean **1.30s**, min 1.20s, max 1.83s |

This confirms §4.1's hypothesis outright: the ~50s/call from §3.2 was almost entirely reload
overhead. Resident, steady-state per-chunk latency (~1.3s for 1.37s of new footage — a real-time
factor under 1) beats the current Wan2.2/DashScope substrate's documented ~30s per 5s clip by
roughly **20×**, and lands close to the north star's original "~1s first-frame" figure. At
$0.602/hr, a chunk costs **~$0.0002**; a 30-turn session (the length of the judged `ab_scenario`)
costs **~$0.009** in GPU time for video generation, plus the one-time $0.0025 pipeline-load —
under a cent, and a rounding error next to the ~$0.55 LLM-side cost `main.py stats` measured for
the same scenario length in plan 03. **Video-gen compute is not the cost center on this substrate.**

### 5.2 Identity drift: worse under the sliding window, and a naive fix failed

The fixed 9-frame conditioning tail buys flat latency, but at that price the model loses the scene
entirely, not just facial detail. By turn 2 the frame is a tight, unrecognizable close-up; by turn
4 the "story" has left the LEGO minifigure altogether and is generating **macro photography of a
human hand assembling bricks on a baseplate** — a coherent, well-formed video, just not a
continuation of anything that came before it. This is a harder failure than §3.3's — that was
framing/detail drift within one identity; this is losing the subject completely within four
~1.4s chunks.

Tried the obvious literature-grounded fix (ViMax's reference-portrait pattern from plan 03's
literature review, §2.1): re-inject the original `legoman.png` as a **second conditioning item**
every turn, at the same start frame as the sliding-window tail, low strength (0.3) so it nudges
without overriding the motion signal. Cost nothing extra (mean 1.33s/turn, same as without it) —
but **did not fix the drift**. By turn 4 the scene had drifted just as far, still with an invented
human hand, just into a different wrong scene (a LEGO-panel/wall setting instead of a baseplate).
A weak same-position anchor isn't enough to pull generation back once the sliding window has
already carried it away from the subject.

**This, not latency, is the open problem the plan now turns on.** A resident server solved speed
convincingly. It did not touch identity. Candidate directions, untested: a much stronger anchor
(trading motion continuity for it), the 13b checkpoint (more capacity may hold the subject longer
even at low anchor strength), or a properly *conditional* anchor scheme closer to what ViMax
actually does — per-shot LLM selection of which reference image to inject, not a fixed weak blend
every turn. Worth noting: this is a substrate-level failure, not something the agent layer can
paper over — no amount of Tolkien/Spielberg prompt discipline fixes the video model forgetting
what the subject looks like.

## 6. 13b fidelity + anchor sweep (2026-09-05, follow-up #2 — done)

Ran three configurations on `ltxv-13b-0.9.8-distilled` (A100 PCIE 80GB, $0.873/hr, **$0.27**
spent), same 8 prompts, same seeds, same sliding-window methodology as §5:

### 6.1 Latency: proportional to model size, still real-time

| | 2b (§5) | 13b |
|---|---|---|
| Pipeline load (one-time) | 15.3s | 12.3s (cached) / 319s (first, includes download) |
| Turn 0 (i2v) | 2.77s | 4.14s |
| Steady-state mean | 1.30s | **3.59–3.64s** |
| VRAM | ~16GB | **38.6–40.7GB** |

3.6s for 1.37s of footage is a real-time factor of ~2.6 — not under 1 like the 2b, but still
comfortably interactive (the LLM calls in the director loop take longer). 80GB VRAM required.

### 6.2 Identity: 13b solves the catastrophic failure, anchor 0.5 is the sweet spot

**13b baseline (no anchor):** Dramatic improvement over 2b. The model holds the LEGO minifigure as
a LEGO minifigure through all 8 turns — no human-hand hallucination, no total subject loss. BUT:
the camera drifts down to a legs-only crop by turn 4, and the torso shifts from red to brown with
invented construction-worker printing. This is *graceful degradation* (framing/costume drift)
rather than *catastrophic failure* (subject identity loss). Still not production-ready on its own.

**13b + anchor 0.5:** **The clear winner.** Subject identity held cleanly through all 8 turns —
yellow smooth head, red torso, blue legs, correct LEGO proportions. No invented hair or facial
detail (the 2b's T0 failure mode, absent here). Prompt steering worked well: the minifigure
actually picks up and holds a red brick (T4), spreads arms in a triumphant pose. Camera framing
stayed on the full figure. The only drift: background migrated from pure white to a LEGO baseplate
+ brick-wall setting by T2, and the torso shifted from bright red to brown-red by T7. Neither is a
subject-identity failure — they're *scene* drift, much easier to address with prompt discipline
(Spielberg can specify background) or a background-reinforcement prompt suffix.

**13b + anchor 0.7:** Subject identity also held through 8 turns, but **re-introduced the
hair-invention artifact** at T2 — brown sculpted hair on what should be a smooth yellow head. The
competing signals between a strong reference anchor and the sliding-window continuation create
hallucinated detail, the same failure mode the 2b showed at T0 but now appearing mid-session.
Prompt steering was also weaker than at 0.5 (less responsive to action instructions). 0.7 is
worse than 0.5 on both axes.

### 6.3 Verdict: viable substrate configuration found

**`ltxv-13b-0.9.8-distilled` + resident pipeline + 9-frame sliding window + anchor at 0.5** is
the first configuration tested that holds subject identity through a full 8-turn session while
also responding to prompt-driven steering. The remaining drift (background, torso hue) is scene
detail, not subject identity — the kind of thing the agent layer (Tolkien/Spielberg prompt
discipline) *can* address, unlike the 2b's catastrophic subject loss which it could not.

This resolves the §5.2 open question: **more model capacity (13b) + moderate anchor (0.5) fixes
the identity problem** that was blocking the substrate choice. The white-void brief isn't quite
free (background drifts to a LEGO-set context), but the subject stays recognizable — good enough
to build the integration on, with background reinforcement as an agent-layer concern.

**Cost:** ~3.6s/chunk on an 80GB A100 at ~$0.87/hr = **~$0.0009/chunk** — still a rounding error
vs LLM costs. 80GB VRAM is the new floor (vs 40GB for the 2b), raising the self-hosting cost from
~$0.60/hr to ~$0.87/hr at current rental rates.

## 7. Follow-ups (not yet done)

- **Longer conditioning tail** (17-25 frames instead of 9) — may reduce background drift further;
  trades flat-cost guarantee for identity. Worth knowing where the knee is.
- **Multi-seed A/B** — is the anchor-0.5 result robust across different seeds, or was this a
  lucky draw? Run 3-5 seeds to confirm.
- **Background reinforcement via prompt** — can Spielberg-style prompt suffixes ("pure white
  seamless background, studio lighting") hold the background where the anchor can't?
- `conditioning_strength` as a per-turn agent decision — the design question is entirely agent-side
  now that a viable substrate config exists.
- **Integration design** — the resident server now needs to become a real service the TurnEngine
  can call, replacing the DashScope i2v backend.
