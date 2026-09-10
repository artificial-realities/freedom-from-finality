# Substrate findings from the LTX-2.5 model card (2026-09-09)

After the full-combination run showed that within-clip drift is not a prompt problem, this session read the LTX-2.5 documentation against our server. Sources: the gated model card of `Lightricks/LTX-2.5-Diffusers` (fetched with the project's token), the diffusers `LTX2ConditionPipeline` source and its `ltx2/utils.py`, the LTX-2 conditioning docs, the IC-LoRA adapter list, and the Ingredients LoRA discussion thread. No GPU was used; everything below is to be verified in the next rental.

## 1. The server has been running the distilled weights with the full-model recipe

The repository ships two transformers:

| Folder | What it is | Recipe the card prescribes |
|---|---|---|
| `transformer/` (default in `model_index.json`) | **distilled** DiT | `sigmas=DISTILLED_SIGMA_VALUES` (8 values), `guidance_scale=1.0`, `audio_guidance_scale=1.0`, `stg_scale=0`, `audio_stg_scale=0`, `modality_scale=1.0`, `audio_modality_scale=1.0`; "passing `num_inference_steps` instead would hand the model a generic linear schedule and quietly cost quality" |
| `transformer_full/` | full / SFT DiT | `num_inference_steps=30`, the pipeline's default guidance (CFG 3.0, STG 1.0, modality 3.0, rescale 0.7, audio CFG 7.0), scheduler `use_dynamic_shifting=True, shift_terminal=0.1` |

`LTX2ConditionPipeline.from_pretrained(MODEL_ID)` loads `transformer/`. Our server did exactly that and then ran 15 linear steps with CFG 3.0 and STG 1.0 on it: the distilled weights, the full recipe, and the shipped scheduler (which has shifting off, for the distilled schedule). Every clip in every review so far was rendered this way.

Consequences:

- **Quality.** This is the most likely answer to "why are my videos worse than the ones on LTX's site": the wrong schedule on the distilled weights, plus single-stage 768×512 where the card recommends 960×544 and a two-stage latent upsample for its best output.
- **Drift.** Classifier-free guidance at 3.0 and spatio-temporal guidance on a model trained to run unguided is a plausible source of exactly the artifacts we see within clips: saturation, glow, coloured light, invented texture and prints. This is a hypothesis until the sweep runs.
- **Latency.** The distilled recipe is 8 forwards per clip. Ours was 15 steps × 3 forwards (CFG negative pass plus STG pass) = 45. Same weights, so roughly a 5× reduction is on the table before any hardware change: the H100's 130–155 s per clip would become 25–35 s.
- **The "broken distilled LoRA" of 2026-09-07** was `ltx-2.5-22b-distilled-lora-450-bf16.safetensors` applied to the already-distilled `transformer/`. That LoRA exists to distil `transformer_full/`; on distilled weights it double-distils and produces the temporal corruption we saw. It is not needed at all.

`server/ltx2_server.py` now has `LTX2_VARIANT=distilled|full` with the two recipes as written on the card, `DEFAULT_NEGATIVE_PROMPT` from diffusers as the default negative, 960×544 as the default size, and no LoRA path.

## 2. A reference anchor at the end of the clip is supported

The server's docstring claimed a second identity condition was impossible because conditions overwrite each other at index 0. That is true only at index 0. In `LTX2ConditionPipeline`, a condition at latent index 0 overwrites the leading tokens; a condition at any other index is appended as a *guiding keyframe* with its own tokens and a conditioning mask, blended in by `strength` at every step. Negative indices wrap, so `index=-1` is the last latent frame (the first-last-frame example in the diffusers docs does exactly this). diffusers' own long-form utilities define `ANCHOR_KEYFRAME_STRENGTH = 0.95`.

So a clip can be conditioned on the reference (or the previous chunk's tail) at index 0 *and* on the reference at the end. The clip then has to arrive back at the sheet, which makes mid-clip drift something the model must undo rather than accumulate. The cost is that every anchored clip ends in the reference pose, which for this blueprint is also the natural rest pose and gives the next `continue` a clean tail. A mid-clip anchor (index 8 of 16 latent frames for a 5 s clip) leaves the ending free at the price of a near-static beat in the middle.

`anchor_strength` / `anchor_index` are now request fields on the server and `ltxv_anchor_strength` / `ltxv_anchor_index` in the client config. Off by default.

## 3. Identity conditioning proper exists: the Ingredients IC-LoRA

Lightricks publish `LTX-2.3-22b-IC-LoRA-Ingredients`, an in-context LoRA that reads a reference sheet (one composite image of the characters, props and location on a black background) as latents and keeps them consistent in the generated clip. There is no 2.5 checkpoint, but Lightricks staff say in the model's discussion thread that it "works great with ltx-2.5" at a LoRA strength around 1.3. In diffusers it runs through the in-context pipeline (`reference_conditions=`), which the LTX-2.5 card also exposes through the modular pipeline. This is the real answer to per-character identity and to the second blueprint's multi-character cast; it is a larger change (a different pipeline and a reference-sheet builder) and comes after the recipe fix and the anchor are measured.

## 4. Prompting

The card: "The model was trained on long, single-paragraph audio-visual captions and degrades on short prompts." Our compiler already produces one long paragraph. Lightricks' guide orders it shot, scene, action, character, camera, audio, in present tense; ours goes style, sound, framing, character, action, dialogue, end image, voice-over. Close enough to leave until the substrate is measured.

## Plan for the next GPU session

1. `server/vast_run.sh up` (distilled by default), then `python scripts/style_sweep.py` for the substrate set: the kick that drifted in every live clip, with no anchor, anchor 0.95 at the end, 0.70 at the end, and 0.95 mid-clip; two clips each (cut, then continue). Contact sheet plus per-clip latency from `X-Latency-S`.
2. Restart the server with `LTX2_VARIANT=full` and run the same set, to see whether the full recipe is worth its 10× cost over distilled.
3. Pick the recipe and the anchor, set them in `configs/director.live.yaml`, and run ten segments through the web player as on 2026-09-09 for a like-for-like comparison with `img/2026-09-09-full-combination.jpg`.
4. If drift survives all of that, the Ingredients IC-LoRA is next.
