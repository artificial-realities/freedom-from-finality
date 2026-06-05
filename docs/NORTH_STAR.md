# North Star

> The technical direction for Artificial Realities now that the IKT469 academic project is finished.
> Decided 2026-06-05. This steers which `EVOLUTION.md` items we pursue and which we drop.
> Optimize for **technical ambition and a genuinely compelling result**, not commercialization/moats.

## The destination

Re-architect Artificial Realities into a **literature-grounded multi-agent director stack** (~3 roles, after FilmAgent / ViMax / MovieAgent / Camera Artist) driving a **self-hosted streaming video substrate**, with an **off-the-shelf memory framework** replacing the hand-rolled Spock summarizer — carrying the *validated findings* from the academic project forward into the new design.

Form-factor stays **watch + steer**: continuous, flowing AI video you lean back to and steer with natural-language input.

## The three big moves

1. **Memory: Spock → off-the-shelf framework.** Replace Spock's full-rewrite prose `narrative_memory` with a real memory layer (Letta is the leading candidate — its self-editing-memory agent maps almost exactly onto Spock's "decide what's worth remembering" role; Mem0/Zep are alternatives). Most self-contained of the three moves.
2. **Agents: 4 roles → ~3 literature-grounded roles.** Collapse Tolkien/Spielberg/Attenborough/Spock into a Director-Writer / Cinematographer / Editor-Continuity split (narration likely folds into the Director-Writer role; TTS becomes a non-agent service). *Proposed shape — to be designed in its own plan.* Carry forward the validated inter-role contracts (see "Findings to preserve").
3. **Substrate: clip i2v → streaming.** Replace Wan 2.2 batch clips + end-frame-PNG chaining with **self-hosted LTXV autoregressive chunk-streaming**.

## Substrate reality (validated 2026-06-05 — read before planning move 3)

The dream of "one running infinite stream you steer mid-flight" is **not** a shipped, self-hostable capability today. What *is* available and self-hostable:

- **LTXV (LTX-Video), autoregressive line:** video generated in **chunks**, each chunk conditioning the next; **~1s first-frame** latency on H100; coherent **>60s** long-form; forward video extension; pose/depth/canny control LoRAs across the scene. Open weights.
- **LTX-2 / 2.3 (DiT diffusion line):** higher-fidelity **batch clips** up to ~20s, 4K@50fps, native synced audio. *Not* autoregressive/streaming. Needs 32GB+ VRAM (A100/H100); distilled/quantized run smaller at lower res.

**Operative reframe:** *fast autoregressive chunk-streaming that you steer **between short chunks***, not a live stream you interrupt mid-frame. This:
- kills end-frame-PNG chaining (replaced by richer chunk/latent conditioning),
- kills the ~30s latency gate (~1s first frame),
- keeps the control loop roughly its current shape (a loop emitting a per-chunk spec) → lower risk for the agent redesign,
- is self-hostable on a rented A100/H100 (~$2–4/hr). User has serious solo time and is open to running a GPU.

**Caveat:** Lightricks' marketing overstates "live steering" vs. what the repo actually exposes (no streaming inference API; chunk-extension is batch-orchestrated). A **hands-on GPU spike** is the real proof of chunk size vs. coherence vs. latency, and of whether LTX-2 fidelity and LTXV streaming combine or force a choice. (Sources in the 2026-06-05 research turn.)

## What this north star PRUNES from EVOLUTION.md

Deciding the direction kills several otherwise-tempting items:
- **Wan 2.2 → Wan 2.7** — dead; we leave the Wan/clip family entirely.
- **Nano Banana / FLUX end-frame authoring** — dead; end-frame chaining itself is being removed.
- **Clip-boundary pacing/span machinery** (`audio_seconds_owed`, concat+mux) — reworked for streaming, not extended.
- **Native in-scene audio** comes from the video model; a separate TTS layer is only needed for *narrator* voice-over (open question — Cartesia/ElevenLabs still relevant there).

## Findings to preserve into the new design

From the academic project (see `docs/findings/`):
- The **one-turn-delayed feedback loop** (Editor's context brief → Director next turn).
- **Deterministic Pydantic state, separate from narration prose.**
- **Role-disciplined state slicing** (each role reads only what it needs).
- A clear boundary between **"what happens"** (Director) and **"how it's shot"** (Cinematographer), and between **current truth** and **rolling memory** (Editor).

## Status

- ✅ Direction decided (2026-06-05).
- 🔜 No-regret infra + cleanup first (`plans/00`), then memory, agent redesign, streaming — see `plans/README.md`.
