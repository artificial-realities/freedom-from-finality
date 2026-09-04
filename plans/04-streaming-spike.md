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
2. **Re-run the identity-fidelity test on `ltxv-13b-0.9.8-distilled`.** The 2b model's drift into
   invented facial detail may not survive at 13b; this determines whether the LEGO-in-white-void
   brief is viable on this substrate at all, or needs art-directed negative prompting / a control
   LoRA.
3. **`conditioning_strength` needs to become a per-turn model decision**, not a constant — likely
   folded into Spielberg's shot spec once the agent side of this integration is designed.

## 5. Follow-ups (not yet done)

- Resident-server latency benchmark (§4.1) — the number that actually decides "is this faster than
  Wan2.2" for the live-play case.
- 13b fidelity re-run (§4.2).
- A longer chain (5+ extensions) to check whether identity drift compounds or stabilizes, and
  whether `conditioning_strength` needs to vary chunk-to-chunk to hold a longer scene together.
- Cost model at scale: this spike cost $0.22 for ~4 short generations; a resident-server sizing
  pass should translate into $/session estimates comparable to the current DashScope Wan2.2 numbers
  in `main.py stats`.
