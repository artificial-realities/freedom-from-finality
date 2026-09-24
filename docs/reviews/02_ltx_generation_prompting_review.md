# 02 — LTX-2.5 generation and prompting review

*2026-09-22. Research and design only; no product code, prompt, schema, config or test was changed. Repository at commit `1de34a0`. Primary sources were read on 2026-09-22; the diffusers pipeline sources are `main` as fetched that day (line numbers below refer to those copies).*

**Evidence labels used throughout.** **[F]** fact from an official Lightricks source (model card, repo, docs, blog). **[C]** derived from reading code (this repository, diffusers, ltx-pipelines). **[M]** measured here on this repository's logs and clips. **[H]** hypothesis. **[E]** needs an experiment (numbered in §10).

---

## 1. Executive recommendation

The project is running the right model, the right weights and the right sampling recipe. What it sends the model is not yet in the model's language, and the way it stitches clips wastes a third of a second at every seam. Six changes, in order of expected impact:

1. **Rewrite the compiler's output as caption prose, keeping the Director schema exactly as it is.** LTX-2.5 was trained on "long, single-paragraph audio-visual captions" [F] and Lightricks' enhancer system prompt spells out the dialect: begin with the action or visual, state the shot type, camera motion and viewpoint *as flowing prose* ("never as 'medium shot, static camera —'"), weave the soundscape into the same paragraph, use "Initially… A moment later…" transitions, no labels, no bullet lists [F]. The current compiler emits three constructs the official guidance names as underperforming: a tag-style camera line (`Medium shot, straight-on, eye level, static camera.`), a labelled cast list (`On screen: …; …`) and a labelled ending (`Final frame: …`) (`src/story/compiler.py:87-97, 114-115`). Every difference between today's prompts and the recommended ones in §6 is a deterministic transformation of fields the Director already writes.
2. **Trim the overlap.** With `continuity_frames: 9` the server overwrites the first two latent frames of every clip with the previous clip's tail, so every clip's first nine frames *are* the previous clip's last nine frames [C]. Measured on the 2026-09-18 run: mean absolute pixel difference across the nine overlap frames is 0.4–2.0 (codec noise; a real nine-frame motion step is 16) and the figure is motionless for those frames [M]. On top of that the player crossfades 250 ms. The viewer sees a 375 ms hold at every seam. Trimming is safe at strength 1.0 because the overlap is a reconstruction of frames already shown. Rendering 153 frames and trimming 9 gives exactly 144 frames = 6.000 s per clip.
3. **Drop the negative prompt from the live config.** On the distilled recipe `guidance_scale=1.0` sets `do_classifier_free_guidance` to False, `encode_prompt` never encodes the negative text, and it is never concatenated into the batch [C, §2.5]. The 2026-09-08 "green-face" negative prompt did its work under the old CFG-3 recipe; today it is dead text that the config comment presents as active.
4. **Split the anchor into an identity-invariant `look_short` and a one-clause style, and test before cutting further.** With the tail conditioned at strength 1.0 the picture already supplies identity, costume, props, pose, framing, background and light for the first 0.375 s; the text has to carry the *motion*, the *camera*, the *sound*, anything that *enters*, and enough identity and style words to keep the model's prior from pulling the remaining 5.6 s toward brows, grins and grey floors. The 2026-09-17 probe showed that zero anchor text drifts by the sixth clip; what has never been tested is *which* anchor words do the holding (E3). Repeated `look_short` becomes an active conflict the moment a costume changes (the red cap of 2026-09-15 versus "bare yellow head"), so it should name only invariants and the compiler should render worn and held items from the world model.
5. **Put the renderer's physical limits in the generic Director prompt, not in the blueprint.** Lightricks: "highly chaotic motion can introduce artifacts; simpler, plausible motion is more reliable (everyday motion like dancing is fine)"; earlier guidance names "jumping, juggling" as risky and says to avoid internal states like "sad" or "confused" without a visible cue [F]. The Director wrote a one-hand breakdance spin and a hundred push-ups for the video model on 2026-09-18; the compiler cannot fix that after the fact. Also from the official duration guidance: the model "won't stretch a moment or add a pause you didn't prompt for", so a fixed 6 s clip needs about 6 s of described action, which is a Director rule with a number in it (`clip_seconds`).
6. **Keep everything else as it is**: the distilled recipe, strength 1.0, nine frames, anchor off, no runtime enhancer, no multi-shot, no LoRA, fixed frame count. Each is justified in §8. Two LTX-native audio facts belong to Prompt 04's decision: the voice-over line renders and is loud (a voice-over clip peaks at −0.4 dBFS against −20.7 dBFS for a silent clip [M]), and audio has *no* continuity across clips because only video frames are carried in the window [C].

Nothing above needs a schema change. Two optional additions that would need approval are listed in §9.

---

## 2. Verified LTX-2.5 behaviour relevant to this project

### 2.1 Model, weights, recipe [F][C]

- LTX-2.5 is a joint audio-video DiT with a fine-tuned Gemma 4 12B text encoder, separate video and audio VAEs, an optional diffusion video decoder, a spatial and a temporal latent upsampler, an optional duration head, and a separate prompt enhancer model [F: model card, LTX-2.5]. The Diffusers pack (`Lightricks/LTX-2.5-Diffusers`) ships `transformer/` (distilled, the default) and `transformer_full/` (SFT) [F].
- The distilled transformer runs an explicit sigma schedule, unguided. Diffusers' `DISTILLED_SIGMA_VALUES = [1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875]` (`utils.py:27`); ltx-pipelines lists the same eight plus a terminal `0.0` (`constants.py:16`). The model card: "Passing `num_inference_steps` instead would hand the model a generic linear schedule and quietly cost quality" [F]. The server passes the sigmas and zeroes every guidance (`server/ltx2_server.py:229-237`); its own log confirms `forwards=8` per clip [C].
- Recommended size and length on the card: 960×544, 121 frames, 24 fps; constraints `num_frames % 8 == 1`, width and height divisible by 32 [F]. The live server runs 640×352 and 145 frames, both legal, the size below the recommendation (Prompt 03 owns that trade).
- Lightricks' *quality* paths are two-stage (half resolution, ×2 latent upsample, three-sigma tail) or DFR (distilled transformer plus a detailing IC-LoRA at fixed strength 0.5, generated keyframe slots, optional temporal ×2/×4) [F: model card; repo README]. The server runs single-stage at full size, the card's "quick start" path.
- Image conditions are re-compressed through H.264 at CRF 18 for LTX-2.5 (33 for 2.3) to match training; multi-frame video conditions are not re-compressed (`pipeline_ltx2_condition.py:903-910`, `utils.py:32-33`) [C].
- The default text length is `max_sequence_length=1024` tokens (`pipeline_ltx2_condition.py:1392`) [C]; the opening prompt is about a third of that.

### 2.2 What a training caption looks like [F]

The most precise official description of the model's prompt dialect is the system prompt Lightricks ships for the LTX-2.5 prompt enhancer (`packages/ltx-core/src/ltx_core/text_encoders/gemma/encoders/prompts/gemma4_i2v_system_prompt.txt`; identical text in diffusers `utils.py:275-305`). It instructs the enhancer to write "in the EXACT style of the training captions used for this video model":

1. "Begin immediately with the action or visual detail. Do NOT use 'The scene opens…', 'We see…', 'There is…'."
2. "Objective, observable description only. Do not infer emotions or intentions."
3. Full visual detail: environment, character appearance, spatial positioning.
4. "For every shot you MUST include, woven naturally into the prose (never as tags or labels): shot type (exactly one: extreme wide shot / wide shot / medium shot / medium close-up / close-up / extreme close-up); camera motion (always stated; if none, explicitly say the camera remains static); camera viewpoint relative to subject (front-facing / back-facing / side view / over-the-shoulder / top-down / low-angle / high-angle). Express these as flowing prose: 'a medium shot frames…, captured from a front-facing angle as the camera slowly pans…'. Never as 'medium shot, static camera —'."
5. "Complete soundscape, integrated naturally: any dialogue (quote it exactly, in the original language), tone of voice, background music, and environmental sounds."
6. "Strict chronological, real-time flow using transitions like 'Initially…', 'A moment later…', 'Simultaneously…'."
7. "One single continuous paragraph. No bullet points, no section headers, no labels like 'Audio:' or 'Visual:'. … roughly 150–220 words."

The image-to-video variant adds: "the opening of your caption must match the reference image exactly — same subject(s), identity, appearance, clothing, setting, lighting, and composition … describe it faithfully, then narrate chronologically as the user's requested action unfolds from it. Never contradict, replace, or invent things not consistent with the image. Single continuous take — no hard cuts."

The Diffusers model card adds the one-line consequence: "The model was trained on long, single-paragraph audio-visual captions and degrades on short prompts."

### 2.3 Official prompting guidance [F]

From the LTX-2.5 prompt guide (ltx.io blog, 2026-08-10) and the docs.ltx.io prompting guide:

- Six elements: establish the shot (cinematography terms, shot scale); set the scene (lighting, palette, textures, atmosphere); describe the action as a natural sequence, "give each sentence a verb that does something … appearance alone gives the model little to animate"; define characters (age, hair, clothing, distinguishing features; "express emotion through physical cues, not abstract labels"); camera movement ("describing how subjects appear after the movement helps the model complete the motion"); audio ("place spoken dialogue in quotation marks; specify language and accent if needed").
- Structure: single flowing paragraph, present tense, "match the level of detail to the shot scale (close-ups need more detail than wide shots)", "describe camera movement relative to the subject", 4–8 sentences for a simple single shot; screenplay style is acceptable for dialogue-heavy scenes. "Don't paste a prompt written for another video model … tag syntax and shot-list formatting don't [carry over], and tend to underperform."
- The repo README's older structure list says "Keep within 200 words" and to start with the main action in one sentence.
- Image-to-video: "For image-to-video from a first frame, prefer a single continuous take unless you intentionally describe a cut away from that opening image."
- Multi-shot (new in 2.5): cuts are written in prose inside one paragraph ("A hard cut transitions to…"), re-establishing framing, re-identifying subjects and stating audio continuity at each cut; 2–4 shots per generation.
- Duration: "LTX-2.5's optional duration predictor sizes the clip to the action you describe and times it as written. It won't stretch a moment or add a pause you didn't prompt for. Write the beats you want into the prompt ('she pauses', 'a beat of silence') … or set an explicit duration."
- Warnings: on-screen text is unreliable; "highly chaotic motion can introduce artifacts; simpler, more plausible motion is more reliable"; the LTX-2 guide lists "non-linear or fast-twisting motion (e.g., jumping, juggling)" as artifact-prone while "dancing can work well", and warns against "scene complexity overload" and "inconsistent lighting logic".
- Enhancer: "We recommend using it when your prompt is short, rough, or was originally written for a different model. It helps least when your prompt already follows the structure detailed in this post. The enhancer runs an extra inference pass, so it adds some latency."

### 2.4 Conditioning mechanics in `LTX2ConditionPipeline` [C]

The server sends one `LTX2VideoCondition` at index 0 (the reference on turn 1, the window afterwards) and optionally the reference again at `anchor_index` (`server/ltx2_server.py:604-623`). In the pipeline:

- **Frames → latent frames.** A condition of *n* pixel frames is trimmed to `(n-1)//8*8+1` frames (`trim_conditioning_sequence`, `:826-841`) and encoded by the causal VAE into `(n-1)//8+1` latent frames. So 1 frame → 1 latent frame; **9 → 2**; 17 → 3; 25 → 4; 8 frames would be trimmed to 1. The client's `continuity_frames` therefore only means what it says on the 8k+1 grid.
- **Index 0 overwrites.** For `index == 0` the condition's packed tokens replace the first `num_cond_tokens` of the sequence and the conditioning mask there is set to `strength` (`apply_first_frame_conditioning`, `:1001-1004`). For a 145-frame clip at 640×352 the sequence is 19 latent frames × 20 × 11 = 4180 video tokens; a 9-frame window fixes the first 440 (10.5%).
- **Strength semantics.** Initial noise is scaled by `(1 - mask) * noise_scale` (`:1230-1231`); in the loop each token's timestep is `t * (1 - mask)` (`:1823`); after each step the prediction is blended `pred * (1 - mask) + clean * mask` (`:2018`). At 1.0 the overlap tokens are frozen and reproduced; at 0.8 they are noised to 0.2·σ, denoised at a reduced timestep and blended 80/20, i.e. *regenerated*, so they no longer equal the previous clip's frames.
- **Non-zero index = keyframe.** A condition at any other index is *appended* as extra tokens with their own positional coordinates and `mask = strength` (`:1172-1221`). Index −1 on a 145-frame clip maps to latent 18 = pixel frame 137; the single-frame keyframe's temporal extent is clamped to one frame (`:1060-1062`). This is the identity anchor: a target the last third of a second must converge on.
- **Resize.** Conditions are scaled so the longer side fills the target and centre-cropped (`:919-930`). The client letterboxes the reference to the server's exact size first (`src/i2v/ltxv.py:255-280`), so nothing is cropped.
- **Window round trip.** The server hands back decoded uint8 frames as the next condition (`server/ltx2_server.py:677-679`); the pipeline re-encodes them through the VAE. The measured seam error of 0.4–2.0 grey levels is the cost of that round trip plus H.264.

### 2.5 Guidance and the negative prompt [C]

`do_classifier_free_guidance` is `(guidance_scale > 1.0) or (audio_guidance_scale > 1.0)` (`:1318-1319`). The server passes 1.0 and 1.0 (`server/ltx2_server.py:231-232`). In `encode_prompt` the negative text is only tokenised and encoded inside `if do_classifier_free_guidance and negative_prompt_embeds is None:` (`:475`); the returned negative embeddings are `None`, the concatenation at `:1681-1683` is skipped, the batch is not doubled (`:1815`), and `video_cfg_delta = 0` (`:1891`). The pipeline's own docstring says the same: "Ignored when not using guidance" (`:1405`). STG (`stg_scale=0`) and modality isolation (`modality_scale=1`) are likewise off (`:1322-1327`). **The negative prompt has no effect on any distilled render this server produces.** It costs nothing either (it is never encoded), so the harm is only the false belief in `configs/director.live.yaml:33-34` and audit §5.54.

Side note: ltx-pipelines' `DEFAULT_NEGATIVE_PROMPT` starts with training-tag tokens ("has_subtitles, has_blurbox, transition from black, …") that diffusers' copy lacks (`constants.py:131-143` vs `utils.py:77-89`). Only relevant if the full variant is ever used.

### 2.6 The prompt enhancer [F][C]

- It is a *separate* model, `google/gemma-4-E2B-it`, not the fine-tuned text encoder; the Diffusers pack lists `prompt_enhancer` and `processor` in `model_index.json` but does not ship them: "they load as `None`" [F: Diffusers card]. The server's `register_modules(prompt_enhancer=None, duration_head=None)` (`server/ltx2_server.py:271-272`) therefore drops nothing on today's pack; the "10 GB" in the docstring is roughly what loading E2B (about 5B parameters with embeddings, bf16) would cost, not what was resident.
- Mechanics (`pipeline_ltx2.py:572-645`): a chat template with the system prompt of §2.2 and `user prompt: <text>`; for I2V the reference image is passed to the vision tower with `User Raw Input Prompt: <text>.`; greedy decoding (`do_sample=False, no_repeat_ngram_size=5`), up to 600 new tokens, seed 10 (`utils.py:113-118`). When enabled in the pipeline it takes the *first* PIL/list-of-PIL condition as the image (`pipeline_ltx2_condition.py:1637-1649`); a uint8 ndarray window, which is what this server passes after turn 1, would not be used as the image.
- Cost: an extra autoregressive pass of up to 600 tokens with a ~5B model per clip, on the critical path if run inline. Latency is unmeasured here [E6]; the official docs say only that it "adds some latency" [F].

### 2.7 Duration head versus dynamic shapes [C][F]

Three different things get conflated in the notes:

- **Model duration prediction.** `duration_head` predicts `num_frames` from the prompt's text tokens when `num_frames` is omitted, with optional `min_seconds`/`max_seconds` bounds, snapped to the 8k+1 grid (`pipeline_ltx2_condition.py:1631-1634, 1696-1704`). The server always passes `num_frames` and unregisters the head, so it is off.
- **Dynamic shapes.** `LTX2_COMPILE_DYNAMIC=1` sets `dynamic=True` on `torch.compile` of the transformer blocks (`server/ltx2_server.py:462-464`) so one compiled graph serves every frame count. It has nothing to do with the model; it is the mechanism that would make variable clip lengths affordable under fp8+compile. Untested (2026-09-16 plan).
- **Project duration ownership.** `clip_seconds` in `DirectorEngine.clip_seconds` (`src/graph/director_engine.py:93-94`) sets the frames the client requests (`src/i2v/ltxv.py:143-145, 181`). The compiler has no duration at all. The Director is told the number in its system prompt; the official guidance that the model "won't stretch a moment" means that number must also govern how much action the Director writes. The runtime policy (fixed versus predicted length, dynamic compile) is Prompt 03's.

### 2.8 LoRA and IC-LoRA facts [F][C]

- diffusers supports `load_lora_weights` / `set_adapters` on the LTX-2 pipelines [F: diffusers docs]. Lightricks' own pipelines run IC-LoRAs on the *distilled* transformer (ICLoraPipeline "can only be used with a distilled model"; DFR's detailing IC-LoRA runs on the distilled weights at strength 0.5; DubIt too) [F: pipelines.md]. `DEFAULT_LORA_STRENGTH = 1.0` (`constants.py:112`).
- "The large majority of LoRAs and IC-LoRAs trained on LTX-2.3 run on LTX-2.5 without changes … validate your adapters before production use" [F: LTX-2.5 card].
- The **Ingredients** IC-LoRA (`Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients`, a reference-sheet identity adapter) recommends strength **1.4**, 30 steps, guidance 4.0, trained at 768×448 × 121 frames on the 2.3 *dev* transformer, with the sheet supplied as a ≥121-frame static video and a two-part `Reference sheet: … / Generated video: …` prompt; "other resolutions, much longer clips … are out of distribution" [F]. The detailing IC-LoRA recommends 1.0 ("the weights ship pre-scaled") [F]. So "1.2–1.4" is one adapter's setting, not a rule [H for anything else].
- A **style LoRA** for this project would be a T2V/I2V LoRA trained with ltx-trainer on the dev transformer on clips in the target look (`training-modes.md`, T2V/I2V modes), then applied to the distilled weights. Compatibility of a PEFT adapter with torchao fp8 quantisation and `compile_repeated_blocks` on this server is unverified [E9].

### 2.9 Multi-shot [F]

Native multishot is a prompt-level capability: cuts written in prose, 2–4 shots, identity re-established at each cut. There is no pipeline flag. It generates the cut *inside* one clip, so the sliding window afterwards continues the last shot; nothing re-seeds. It conflicts with the product's continuous-camera rule ("The picture never cuts", `src/prompts/director.system.md:68`) and with the I2V guidance to prefer a single take from a first frame. It does not change steering latency either way.

### 2.10 Audio [F][C][M]

- Video and audio are generated jointly; the vocoder outputs 48 kHz [F]. The distilled recipe runs audio guidance at 1.0 (the SFT default is 7.0 with the note "authors recommend higher audio guidance" [F: diffusers docs]), so on distilled the prompt is the only lever on audio.
- Official form for speech: quoted line, speaker and voice quality in prose ("he speaks with a deep male voice and a satisfied tone, saying, 'I think it's so good.'"), language/accent when needed; off-screen speech is cued in prose ("Reporter (off-screen, shouting over the noise): …") [F: repo README, guides].
- No official guidance on narration/voice-over as such. The training-caption style treats every voice as a described sound source.
- The window carries video frames only (`server/ltx2_server.py:677-679`); audio latents start from fresh noise every clip (`prepare_audio_latents`). Audio therefore has **no continuity** across clips: ambient beds restart, and loudness is whatever each clip's prompt yields. Measured on 2026-09-18: turn 6 (voice-over) peak −0.4 dBFS, RMS −13.7; turn 3 (no voice-over, "quiet plastic clicks") peak −20.7, RMS −48.9 [M]. The player's 250 ms audio crossfade (`src/web/player.html:188-191`) is the only smoothing.

---

## 3. Current pipeline and parameter assessment

### 3.1 The path of one clip [C]

1. The Director returns a `BeatPlan` (`src/story/schemas.py:81-92`) whose `ClipSpec` (`:61-72`) carries `action`, `end_state`, `camera_motion`, `ambient_sound`, `on_screen`, `dialogue`, `entity_ops`; `framing` and `voiceover` are per beat.
2. `DirectorEngine.emit` applies the clip's ops, then calls `compile_clip(beat, i, world, visual_style, visual_anchor, narration_mode, voiceover_style, opening=(seg == 1), segment_no=seg)` (`src/graph/director_engine.py:250-262`).
3. `compile_clip` (`src/story/compiler.py:66-127`) concatenates, in this order: style prefix (full `visual_style` on the opening clip, else `visual_anchor` or the style's first sentence, `:123-127`); ambient sound as a sentence (`:85-86`); `"{framing}, {camera_motion} camera."` (`:87-89`); `"On screen: "` + entities, characters first, full look if `opening or first_seen == segment_no`, else `look_short` or name, capped at six (`:91-97`); on the opening clip `"The shot opens with {name} {reference_pose}, then the action begins."` (`:101-105`); the action (`:106-107`); each dialogue line as `{Name} says (delivery): "line"` (`:108-113`); `"Final frame: {end_state}"` (`:114-115`); on the beat's first clip with `narration: "ltx"`, `A narrator's voice-over (English, {voiceover_style}) says: "{line}"` as a second paragraph (`:118-121`).
4. The runner passes the prompt and `duration_seconds` to `LtxvI2V.synthesize` (`src/eval/runner.py:596-606`), which posts `prompt`, `continuity_strength`, `continuity_frames`, `num_frames = snap(duration*fps)`, optional `negative_prompt`, `seed`, `anchor_*`, and the letterboxed reference on the first call (`src/i2v/ltxv.py:177-191`).
5. The server builds the conditions (§2.4), draws a seed from `os.urandom` unless one was sent (`server/ltx2_server.py:625-628`), calls the pipeline with the distilled kwargs and `negative_prompt=req.negative_prompt or _negative_default` (`:636-648`), keeps the last `continuity_frames` decoded frames as the window (`:677-679`), and encodes *all* frames to MP4 (`:707-734`).

### 3.2 Parameters as they stand

| Parameter | Today | What actually happens | Assessment |
|---|---|---|---|
| Weights / recipe | `transformer/`, 8 sigmas, all guidance off | Matches the card exactly [C][F] | Correct. The 2026-09-15 finding stands. |
| Size / frames | 640×352, 145 frames (6.04 s) | Legal; below the 960×544 recommendation; single-stage | Prompt 03's trade. Identity held at this size on 2026-09-18. |
| `continuity_frames: 9` | 9 pixel frames → 2 latent frames overwritten | First 9 output frames reproduce the previous tail; **measured duplicate** (§3.3) | Keep 9 for motion context; **trim** the overlap. Values must be 8k+1. |
| `continuity_strength: 1.0` | Overlap tokens frozen | Steering lands in the other 17 latent frames through the prompt; 2026-09-18 shows lines landing within the clip (breakdance at 20, push-ups at 28, monster at 35) | Keep 1.0. Lowering it regenerates the overlap and breaks the seam identity (§2.4); only worth testing with the overlap *kept* and crossfaded [E4]. |
| `anchor_strength: 0.0` | No keyframe | Verified as an end-frame target (§2.4); the sweep's "suppresses closing action" follows from the mechanism | Keep off. Use deliberately only for "must end on this frame". |
| Negative prompt | 32-term string in the live config | Never encoded (§2.5) | Remove from the live config; keep the server field for `LTX2_VARIANT=full`. |
| Seed | random per clip (`os.urandom`) | Fresh noise per clip and per audio track | Keep random or derive per `(session, segment)` for reproducible reruns; a *fixed* seed would reuse identical initial noise on every clip [H: repeated textures, identical audio noise], see §8. |
| Reference handling | letterboxed to server size, subject 80% of height, border-colour fill | Re-compressed at CRF 18 by the pipeline; no crop | Correct. |
| Prompt enhancer | not loaded | not shipped in the pack anyway | Keep off at runtime (§7, §8). |
| Duration | fixed `clip_seconds: 6` | duration head unregistered; `num_frames` always sent | Fine; the Director must write ~6 s of action (§1.5). |
| Warm-up prompt | a Lego sentence in the server (`server/ltx2_server.py:486-487`) | Story-specific text in generic server code | Cosmetic; the server should warm up on a neutral sentence. |

### 3.3 The seam, measured [M]

On `logs/live-box/video/turn_*_20260918_*.mp4` (63 clips, 145 frames each), mean absolute RGB difference between clip N's last nine frames and clip N+1's first nine frames, frame by frame:

| Seam | Overlap-frame diff (9 values) | Consecutive-frame diff inside clip N+1 (frames 9–30) | Last frame of N vs frame 9 of N+1 |
|---|---|---|---|
| 2→3 | 0.4 ×9 | 1.9 | 0.5 |
| 3→4 | 0.4 ×9 | 0.2 | 0.4 |
| 12→13 | 0.6–0.7 | 0.5 | 0.8 |
| 20→21 (breakdance) | 1.3–2.3 | 3.3 | 16.5 |
| 35→36 (monster) | 1.4–1.5 | 2.3 | 1.6 |
| 48→49 (transformation) | 1.8–2.1 | 16.0 | 16.3 |

Clip 3's own consecutive-frame differences are 0.1 for frames 0–8 and 2–6 from frame 9 on: the overlap is not only a repeat, it is a *still* repeat, then the action starts. With a 250 ms crossfade on top, every seam is a 375 ms freeze. This is the single most visible artefact of the current stitching and it is not a model limit.

### 3.4 Other code-level observations [C]

- **Render failure desynchronises picture and world.** A failed render is skipped (`src/i2v/ltxv.py:162-163`; audit §13.5), the world has already applied that clip's ops, and the window still holds the *previous* clip's tail. The next prompt then describes a state the picture never reached. The 01 review noted the world half; the LTX half is that the window cannot skip.
- **`DESCRIBED_IN_FULL` is nearly inert** (agreeing with the 01 review): props have no `look_short`, so a continuing clip names them anyway.
- **Held and worn props never reach the prompt.** The world knows `holder` and `status` (`src/story/world.py:38-40`); the compiler renders neither. On 2026-09-18 turn 55 the prompt says only "princess crown" in a list while the action alone carries "on his head".
- **The reference pose sentence is right in spirit and wrong in form**: "The shot opens with Lego Man standing upright …, then the action begins" is a stage direction; the I2V caption rule is to *describe* the opening frame and narrate from it.
- **Frame-count snapping** is duplicated on both sides (`src/i2v/ltxv.py:233-235`, `server/ltx2_server.py:884-886`); harmless.

---

## 4. Quantitative prompt analysis

Data: the 64 compiled prompts of the 2026-09-18 live run (`logs/live-box/director-ltxv_live_20260918_145552.md`, "Shot" sections), the only live run of the beat engine. Tokens are approximate: measured with the cl100k tokenizer because the Gemma tokenizer is not installed here; Gemma's 262k vocabulary usually yields slightly fewer tokens on English prose.

### 4.1 Composition of a continuing clip (mean over 63 clips) [M]

| Category | Chars | ~Tokens | Share of chars | Source |
|---|---|---|---|---|
| Style anchor (`visual_anchor`, fixed) | 209 | 43 | 28.6% | blueprint |
| Character look (`look_short`, fixed) | 142 | 35 | 19.4% | blueprint |
| Framing + camera line | 55 | 13 | 7.5% | `framing`, `camera_motion` |
| Other on-screen entities (names, descriptions on introduction) | 26 | 8 | 3.6% | world |
| Ambient sound | 38 | 8 | 5.2% | `ambient_sound` |
| Action | 136 | 30 | 18.6% | `action` |
| End state | 85 | 20 | 11.6% | `end_state` |
| Voice-over (15 of 63 clips; 149 chars when present) | 36 | 8 | 4.9% | `voiceover` |
| **Total** | **732** | **162** | 100% | 118 words (94–156) |

**Fixed text is 55.5% of every continuing prompt** (anchor + look + framing line). Motion and its result (action + end state) are 30%; sound 5%; camera 7.5%. The opening clip is 1,569 chars, ~346 tokens, 262 words: above the README's 200-word guidance and the enhancer's 150–220-word target.

The framing line is `Medium shot, straight-on, eye level, static camera.` on 52 of 63 clips. The camera moved on eight clips, always as a push-in, and four of those produced ungrammatical text because `camera_motion` is glued to the word "camera": "slow push-in toward the pair camera", "slight push-in for comedic effect camera".

### 4.2 The exact current prompts, by clip type [M]

**Opening (turn 1, 262 words, voice-over, seeded from the reference):**

> Clean product photograph of a single LEGO minifigure on a seamless pure white cyclorama: no background, no horizon, no floor seam, no walls, no objects behind him. Even, shadowless studio light from all directions; the only shadow is a faint soft contact shadow directly under the minifigure. Flat neutral colours, no coloured light, no glow, no texture. The background stays pure white for the entire shot. The minifigure is the only fixed element on screen; props come and go as the story requires. Motion has a slight stop-motion feel: every click, snap and pop-off reads clearly. No on-screen text, no camera flourish. Cuts and zooms only when they genuinely serve a gag. Soft plastic clicking as the arm moves. Medium shot, straight-on, eye level, static camera. On screen: Lego Man (the most default LEGO minifigure: bare yellow head with no hair piece, printed only with the classic two-dot eyes and curved smile, plain red torso with no print, plain blue legs, claw hands). The shot opens with Lego Man standing upright in a wide product shot, facing the camera, arms straight at his sides, feet together, centred on a white background, then the action begins. Lego Man stands still for a moment, then slowly lifts his right arm, curiously inspecting it. He wiggles it up and down, then lowers it again. Final frame: He stands with his right arm lowered, looking down at it. A narrator's voice-over (English, warm, unhurried documentary narrator) says: "With no one around and nothing but endless white, our subject tests the limits of his small, plastic form."

**Continuation, held state (turn 13, 110 words):**

> Clean product photograph of a single LEGO minifigure on a seamless pure white cyclorama, even shadowless studio light, pure white background, flat neutral colours, a slight stop-motion feel, no on-screen text. Plastic body settling, then silence. Medium shot, straight-on, eye level, static camera. On screen: Lego Man (the same plain LEGO minifigure: bare yellow head with two-dot eyes and a curved smile, plain red torso, plain blue legs, claw hands). He slowly lowers his back, lying flat on the white floor, arms by his sides. He remains still, as if listening for something in the silence. Final frame: He lies flat on his back, arms at his sides, staring upward.

**Viewer-steered (turn 20, "start breakdancing", 137 words):**

> [anchor] Soft plastic clicks and scraping as he moves. Medium shot, straight-on, eye level, static camera. On screen: Lego Man (the same plain LEGO minifigure: …). Lego Man lowers his raised arm, plants both hands on his hips, then looks left and right as if psyching himself up. He suddenly drops to the ground, balancing awkwardly on one hand and one foot as he tries to spin. Final frame: He is mid-spin, one hand and one foot touching the ground, the rest of his body twisted in a breakdance pose.

**Entity introduction + viewer (turn 35, "a monster shows up and he needs to fight it", 156 words, voice-over):**

> [anchor] A faint plastic rattle as the monster appears. Medium shot, straight-on, eye level, static camera. On screen: Lego Man (the same plain LEGO minifigure: …); lego monster (A chunky, blocky LEGO monster: squat, made from basic bricks, two tall red eyes, blocky arms, and jagged "teeth" made from white sloped pieces). As Lego Man sits, a blocky, monster-shaped LEGO creation quietly appears a few feet in front of him, facing him. He freezes, startled, arms raised cautiously. Final frame: Lego Man sits upright, arms lifted in defensive surprise, facing the monster. A narrator's voice-over (English, warm, unhurried documentary narrator) says: "A new arrival: the challenger enters the void."

What rendered (`logs/live-box/video/turn_0035_20260918_145706.mp4`, frames 0/36/72/108/144): a second *minifigure* in a black printed tuxedo with a red-eyed brick head, the yellow figure's face acquiring raised brows and an open grin, and grey speckling spreading over the floor [M]. The world rule "no other minifigures ever appear, by any mechanism" was broken by the renderer's reading of "blocky arms" + "teeth" + a figure-sized prop.

**Scene transition (turn 38, first clip of scene 2, 107 words):** identical in form to any continuation; the transition is invisible to the renderer, which is correct under the no-cut rule. It names the prop bare ("lego monster") where the guide asks to re-identify recurring subjects with the same identifiers.

**Prop state change (turn 55, 117 words):** `On screen: Lego Man (…); lego princess; princess crown.` then "He gently lowers the crown onto his head…". The list gives no state; the action carries everything.

**Dialogue (synthetic; the Lego story has no speaking character).** Run through the real compiler with a two-character world (`compile_clip`, `narration_mode="ltx"`):

> Cold coastal drama, 35 mm film grain, blue hour light. Wind against the glass, a distant foghorn. Medium close-up, side view, slow push-in camera. On screen: Mara (the same grey-haired woman in the navy sweater); Tomas (the lanky man in the yellow raincoat). Mara turns from the window toward Tomas and rests a hand on the brass lamp housing. Mara says (quiet, hoarse): "It went out at two. I counted the ships." Tomas says: "How many?" Final frame: Mara faces Tomas, hand on the lamp, Tomas in the doorway. A narrator's voice-over (English, warm, unhurried documentary narrator) says: "Some nights the sea keeps its own count."

The quoted-dialogue syntax itself is close to the official form (`X says …, "line"`); the parenthetical delivery matches the screenplay style the guide accepts. Two voices in one 6 s clip (two speakers plus a narrator) is the problem, see §7.5.

### 4.3 Contradictions and vague phrasing found [M]

1. **Static list versus entrance.** Turn 35 lists the monster under "On screen" (present from frame 0, contradicting the window, which has no monster) while the action says it "quietly appears". The same pattern on every introduction.
2. **Labels and tags** (`On screen:`, `Final frame:`, `Medium shot, …, static camera.`) on every clip: the form the guide says underperforms and the enhancer is told never to produce.
3. **Internal states** the guide says to avoid: "curiously inspecting", "as if psyching himself up", "He freezes, startled", "in defensive surprise", "as if listening for something".
4. **Chaotic motion**: a one-hand-one-foot spin (turn 20), "100 pushups" (turn 28); four separate movements in the 6 s of turn 20.
5. **Negations and instructions inside a positive caption**: the opening style carries eleven "no …" clauses and two director's notes ("props come and go as the story requires", "Cuts and zooms only when they genuinely serve a gag") that describe nothing visible. The anchor repeats "no on-screen text" 63 times. Whether "no X" primes X in this model is unverified [H], but the words are certainly not caption style.
6. **Repeated description**: turn 49 describes the princess in the list *and* in the action; turn 35 the monster likewise.
7. **Costume conflict potential**: `look_short` says "bare yellow head" on every clip; the moment a hat or crown sits on the head the anchor argues with the window (the 2026-09-15 cap).
8. **Ungrammatical camera line** whenever `camera_motion` is a phrase (four clips).
9. **Ambient sound as a detached fragment** at the head of the paragraph ("Plastic body settling, then silence.").
10. **Voice-over length**: the 15 lines average 149 characters (~25 words); at a documentary pace that is 8–10 s of speech for a 6 s clip. Whether the model rushes, truncates or spills is untested [E7]; the measured peak of −0.4 dBFS says it does try.

---

## 5. Current versus recommended prompt anatomy

| Slot | Today | Recommended (continuing clip) | Why |
|---|---|---|---|
| 1. Style | Fixed 30-word anchor sentence first | One clause fused into the shot sentence ("A clean stop-motion product shot on a seamless white cyclorama") | Style words early are fine [F: "named early in the prompt"], but as a clause, not a preamble of negations |
| 2. State the picture opens on | `On screen: Name (look)…` list | "*Name*, *look_short invariants*, is *posture*, *position*, *activity*, holding/wearing *X*, exactly as the shot opens" from `world.now()`, `holder`, `status` | The I2V rule: open by describing the conditioned frame faithfully. Uses world data the compiler already has; no schema change |
| 3. Shot and camera | `Medium shot, straight-on, eye level, static camera.` | "A medium shot frames him from a front-facing angle at eye level and the camera remains static." / "…as the camera slowly pushes in toward the pair." | The enhancer's mandatory "framing triple" as prose [F] |
| 4. Action | As written, ambient sound detached in front | As written, in chronological sentences with the sounds woven in ("…with a soft plastic click") and "Initially / A moment later" when there are two movements | Caption style [F]; verbs per sentence [F] |
| 5. Entering entities | Listed as present, described in parentheses | Introduced *inside the action* at the moment they enter, with their `description` as an appositive; never listed as present on their first clip | Resolves contradiction 1 |
| 6. Dialogue | `Name says (delivery): "line"` | "*Name* says in a *delivery* voice, "line"" (language/accent from the blueprint when set) | Official form [F]; parenthetical is acceptable but prose is safer |
| 7. End state | `Final frame: …` | "The shot ends with *end_state*." | Same information, caption form; the guide's "describe how subjects appear after the movement" [F] |
| 8. Voice-over | Second paragraph, `A narrator's voice-over (English, style) says: "…"` | Same paragraph: "An unseen narrator with a *style* voice says, "…"" | One paragraph [F]; "unseen/off-screen" is the guide's own cue for a voice without a visible source |
| Length | 118 words mean, 55% fixed | 80–120 words, of which ≤30 fixed (identity invariants + one style clause) | Above the "degrades on short prompts" floor [F], below the 200-word ceiling [F]; the exact anchor length is E2/E3 |
| Opening clip | 262 words, style paragraph of negations + stage direction | 150–200 words: the style rewritten as positive caption prose in the blueprint, the reference pose *described* as the opening frame, then the action | Enhancer target [F]; I2V rule [F] |

Ordering answer: the current order (sound, camera, entities, action, dialogue, end state, narration, style-first) is not what the sources describe. The recommended order is style clause → opening state → shot/camera → chronological action with sound → dialogue in its place in time → ending → narration, all in one paragraph.

---

## 6. Before / after examples

Each "after" is what the recommended compiler would produce from the *same* `ClipSpec` fields (plus world state), except where noted **[Director]**, which marks a change only the Director's writing (under the §1.5 rule) can make. Word counts in brackets.

### 6.1 Opening clip (turn 1) [262 → 181 words]

> A clean stop-motion product shot of a single LEGO minifigure standing centred on a seamless pure white cyclorama under even, shadowless studio light, a faint soft contact shadow beneath his feet and a flat, matte, evenly lit white field everywhere else. He is the most basic minifigure: a bare yellow head with no hair piece, printed only with two dot eyes and a small curved smile, a plain red torso with no print, plain blue legs and yellow claw hands. A medium shot frames him from a front-facing angle at eye level and the camera remains static. Initially he stands upright with his arms straight at his sides and his feet together. A moment later he slowly raises his right arm, the shoulder giving a soft plastic click, and holds the claw hand in front of his face, turning it slowly. He wiggles the arm up and down twice with small clicks, then lowers it to his side and tilts his head down toward it, and the shot ends with him standing still, arm lowered, head tilted down. The studio is silent apart from those clicks. An unseen narrator with a warm, unhurried documentary voice says, "With no one around and nothing but endless white, our subject tests the limits of his small, plastic form."

Differences: the style is rewritten as positive caption prose (blueprint text, story-specific) and the eleven negations and two director's notes are gone; the reference pose is *described* as the opening frame instead of announced; "curiously inspecting" becomes an observable ("turning it slowly"); the framing triple is prose; the sound is inside the action; the ending is a sentence; the narrator is an unseen voice in the same paragraph. The voice-over line is kept verbatim; at ~20 words it is the one thing here the Director should have written shorter **[Director]**.

### 6.2 Held state (turn 13) [110 → 79 words]

> A clean stop-motion product shot on a seamless white cyclorama. The plain LEGO minifigure with the two-dot face, red torso and blue legs is sitting on the white floor with his legs outstretched, centred in frame, exactly as the shot opens. A medium shot frames him from the front at eye level and the camera remains static. He slowly lowers his back until he lies flat, his arms settling at his sides with a soft plastic tap, and then he lies completely still, face up, for the rest of the shot. The studio is silent apart from that tap.

Differences: the 30-word anchor becomes an 8-word clause; the look is reduced to invariants; the opening state comes from the world's `posture`/`activity`/`position` (which were "sitting, legs outstretched; testing the ground with a claw" at the end of turn 12); "as if listening" is dropped; "Final frame" becomes "for the rest of the shot", which also tells the model the clip is a hold.

### 6.3 Viewer-steered (turn 20, "start breakdancing") [137 → 104 words]

> A clean stop-motion product shot on a seamless white cyclorama. The plain LEGO minifigure with the two-dot face, red torso and blue legs stands centred with his right arm raised, exactly as the shot opens. A medium shot frames him from the front at eye level and the camera remains static. He lowers the arm and plants both claw hands on his hips with two small clicks, then turns his head left and right. A moment later he drops into a low crouch, places one hand flat on the white floor and starts a slow, stiff spin on his back with his rigid legs held straight up, plastic scraping softly on the floor, and the shot ends mid-spin with his legs in the air.

Differences: compiler: state clause, prose triple, sound woven, prose ending. **[Director]**: "as if psyching himself up" → "turns his head left and right"; the one-hand-one-foot spin → a back spin with stiff legs, a plausible LEGO move (the guide: dancing fine, fast-twisting risky); three movements instead of four for 6 s.

### 6.4 Entity introduction + viewer (turn 35, monster) [156 → 134 words]

> A clean stop-motion product shot on a seamless white cyclorama. The plain LEGO minifigure with the two-dot face, red torso and blue legs sits upright on the white floor, centred, exactly as the shot opens, and the floor in front of him is empty. A medium shot frames him from the front at eye level and the camera remains static. A moment later a squat monster built from loose LEGO bricks, no taller than his shoulder, with two tall red brick eyes, a row of white sloped pieces for teeth and no minifigure parts, slides into frame from the right with a hollow plastic rattle and stops a short distance in front of him. He raises both claw hands in front of his chest and leans back slightly, holding the pose, and the shot ends with the two facing each other. An unseen narrator with a warm, unhurried documentary voice says, "A new arrival: the challenger enters the void."

Differences: the monster is *not* listed as present; it enters inside the action with its description as an appositive (compiler, from `first_seen == segment_no` and `entity_ops[].description`); "the floor in front of him is empty" states what the window shows; "no minifigure parts" and "no taller than his shoulder" are what the Director should have written given the world rule and what actually rendered **[Director]**; "startled"/"defensive surprise" become posture.

### 6.5 Scene transition (turn 38) [107 → 93 words]

> A clean stop-motion product shot on a seamless white cyclorama. The plain LEGO minifigure with the two-dot face, red torso and blue legs stands centred with both fists raised, facing the squat brick-built monster with the red brick eyes a short distance in front of him, exactly as the shot opens. A medium shot frames both from the front at eye level and the camera remains static. He sidesteps slowly to the left with small plastic footsteps, circling the monster, which turns in place with stiff, clunky quarter-turns to keep facing him, both keeping their distance, and the shot ends with the two still circling, arms raised.

Differences: the prop is re-identified with its identifiers ("squat brick-built monster with the red brick eyes") from `look`/`description` rather than named bare; the state clause carries the monster's `status`; nothing marks the scene change, correctly.

### 6.6 Prop state change (turn 55) [117 → 88 words]

> A clean stop-motion product shot on a seamless white cyclorama. The plain LEGO minifigure with the two-dot face, red torso and blue legs stands centred holding a small bright blocky LEGO crown above his head in both claw hands, the blocky LEGO princess figure standing still beside him, exactly as the shot opens. A medium shot frames them from the front at eye level and the camera remains static. He lowers the crown onto his head, releases it with a careful click and pauses, testing whether it stays, and the shot ends with the crown sitting slightly askew on his head as he stands still.

Differences: `holder`/`status` from the world model render the crown's state ("holding … above his head"); the princess is re-identified.

### 6.7 Dialogue (synthetic) [95 → 84 words, voice-over removed]

> A cold coastal drama on 35 mm film grain in blue-hour light. Mara, the grey-haired woman in the navy sweater, stands at the lamp room window with her hands on the rail, and Tomas, the lanky man in the yellow raincoat, stands in the doorway. A medium close-up frames her from the side as the camera slowly pushes in. Wind presses against the glass and a foghorn sounds far off. She turns from the window toward him, rests a hand on the brass lamp housing and says in a quiet, hoarse voice, "It went out at two. I counted the ships." Tomas answers, "How many?" The shot ends with Mara facing Tomas, her hand on the lamp, Tomas still in the doorway.

Differences: dialogue in the official form with the delivery as prose; the narrator line is *not* folded in because three voices in six seconds is the thing the model is least likely to render cleanly; where narration goes when a clip has dialogue is Prompt 04's decision (TTS mix, or the compiler's rule "no voice-over on a clip with dialogue").

---

## 7. Recommendations by element

### 7.1 `look_short`

Keep it, but make it **invariants only** (what never changes: the two-dot face, the plain red torso, the blue legs, the claw hands) and let the compiler add worn and held items from the world model. Today's "bare yellow head" is an invariant only until a hat exists. Whether the identity words or the style words are what holds the face over long chains is the open question (E3); the 2026-09-17 no-anchor probe grew brows by clip six, and turn 6 and turn 35 of the 2026-09-18 run grew brows and a grin *with* the anchor present [M], so the anchor is necessary but not sufficient. "Helpful reminder, redundant tokens or active conflict?" Reminder for identity (probably necessary), conflict for anything that can change.

### 7.2 `visual_anchor`

Keep a **style clause**, not a style sentence, and rewrite it as positive caption prose ("a clean stop-motion product shot on a seamless white cyclorama under even, shadowless light"). Its job on continuing clips is art direction for regions the window does not cover and for the model's prior over 5.6 s of free generation; the greying floor at the end of busy clips is exactly that prior leaking in. Compared with a style LoRA: the LoRA would carry the look with zero tokens and no possibility of contradicting the window, but it needs training clips in the look, a training run on the dev transformer, and an unverified stack (LoRA + torchao fp8 + compile) on the server; and it is per story, so on a many-story platform it is a production pipeline. A text anchor is the MVP answer; a LoRA is E9, only if E3 shows text cannot hold the style.

### 7.3 Entity introductions

Introduce inside the action at the moment of entry, with the description as an appositive, and never list a `first_seen == segment_no` entity as already present. The blueprint's rule that props "simply appear in frame" or are carried in is a story rule; the caption still has to say *how* the thing enters, because a static list contradicts the conditioned frames. A returning dormant prop (the 01 review's `first_seen` bug) should get its identifiers again on the clip it re-enters (the guide's "reuse the same visual identifiers").

### 7.4 Motion

Do not change the schema. `action` + `end_state` + `camera_motion` + `framing` are enough; the compiler should turn them into the caption's chronological prose and the Director's prompt should carry three generic renderer rules: one plausible movement per clip (two at most, joined by "a moment later"), no internal states without a visible cue, and roughly `clip_seconds` of described action because the model does not pad or stretch. `camera_motion` should be asked for as a verb phrase ("slowly pushes in toward him") so the compiler can write "as the camera {motion}".

### 7.5 Dialogue

The compiler's form is nearly right; move the delivery into prose ("says in a quiet, hoarse voice"). Add language/accent only when the story needs it; that is a per-character blueprint field (`Character.voice`, story-specific, see §9), not a schema field. Do not put a narrator and dialogue in the same clip prompt; one voice per six seconds is the safe rule and the arbitration belongs to Prompt 04.

### 7.6 Sound

Weave `ambient_sound` into the action sentence it belongs to, and end quiet clips with an explicit "the studio is silent apart from …" so the audio branch has an instruction (audio guidance is 1.0 on distilled; the prompt is the only lever). Expect no continuity across clips (§2.10) and a loudness jump between a narrated and a silent clip of about 35 dB [M]; that is an LTX boundary fact, not a prompt fix.

### 7.7 End state

Keep the field; render it as "the shot ends with …" (or "for the rest of the shot" when it equals the opening state). The guide's "describing how subjects appear after the movement helps the model complete the motion" is this field's justification [F].

### 7.8 Style LoRA

Not now. Facts: adapters load on the distilled weights in Lightricks' own pipelines; 2.3 adapters mostly run on 2.5; strengths are adapter-specific (1.4 for Ingredients on 2.3-dev with CFG, 1.0 for the detailer, 0.5 for DFR's detailing pass); nothing supports a generic 1.2–1.4. Unknowns: training data for the look, PEFT on torchao-quantised compiled blocks, speed cost. The Ingredients IC-LoRA is the wrong tool for a continuous chain (it re-establishes identity from a sheet with a 121-frame reference video, doubling the tokens, in a 768×448 bucket); it would matter only if cuts came back.

---

## 8. Parameter decision matrix

| Parameter | Decision | Basis | Confidence |
|---|---|---|---|
| Seed | Random per clip, or derived per (session, segment) for reproducible reruns; **not fixed** | A fixed seed reuses identical initial noise on every clip and every audio track (`randn_tensor(generator=…)` per call); seeds fix the sample, they do not make the free region more controllable [C]; repeated-pattern risk [H] | Medium; E5 |
| Negative prompt | Remove from `director.live.yaml`; keep server field for `full` | Dead path at `guidance_scale=1.0` [C] | High |
| Continuity strength | 1.0 | Overlap identity; steering lands via prompt in 17 free latent frames (2026-09-18) [C][M]; <1.0 regenerates the overlap [C] | High for 1.0; E4 for 0.9 with overlap kept |
| Continuity frames | 9 (2 latent frames); trim the overlap; 17 and 25 are legal (3, 4 latent frames) and only worth testing with trimming | Frame-count grid [C]; seam measurement [M] | High on mechanics; medium on 9 vs 17 (E4) |
| Overlap trimming | Trim 9 frames (render 153, deliver 144 = 6.000 s) | Measured duplicate and freeze [M]; safe only at strength 1.0 [C] | High |
| Anchor strength | 0.0 | End-keyframe mechanism [C]; sweep result [M, 2026-09-15] | High |
| Prompt enhancer | Off at runtime; use offline (E6) | Not shipped [F]; extra pass on the critical path [F]; "helps least when the prompt already follows the structure" [F]; its I2V system prompt re-describes the frame, which a continuation must not | High |
| LoRA | None now; E9 later | §7.8 | Medium |
| Multi-shot | None | §2.9 | High |
| Dynamic duration | Off; the facts are in §2.7; policy → Prompt 03 | [C][F] | — |
| Render size | 640×352 stays until Prompt 03 revisits | Below recommendation [F]; identity held [M] | — |

---

## 9. Proposed target interface

**Director schema (`src/story/schemas.py`): no change required.** The transformation below uses `intent`, `framing`, `clips[].action/end_state/camera_motion/ambient_sound/on_screen/dialogue/entity_ops`, `voiceover`. Two optional additions, **each requiring explicit approval**:

- **A1** `Character.voice: str = ""` in the blueprint model (`src/models/story.py`) and `Entity.voice` in the world, rendered as "says in a … voice" and carrying language/accent for multilingual stories. Story-specific data, generic mechanism.
- **A2** `ClipSpec.camera_motion` documented as a verb phrase in `director.system.md` (prompt wording only, no schema change; listed here because it changes the field's contract).

**Compiler (`src/story/compiler.py`), deterministic, in this order:**

1. Style clause: opening → the blueprint's `visual_style` (to be rewritten by the story author as positive caption prose); continuing → `visual_anchor` as a clause.
2. Opening-state clause from the world: for each on-screen character `name, look_short (invariants), posture, position, activity, holding/wearing {held props by status}`; for props their `status`; `exactly as the shot opens` (on the opening clip: the `reference_pose` described, not announced).
3. Framing triple as prose: shot type and viewpoint parsed from `framing` (comma-split), `camera_motion` → "the camera remains static" / "as the camera {verb phrase}".
4. Action verbatim, with `ambient_sound` attached as a clause to the first movement sentence, and entering entities (`first_seen == segment_no`) inserted at their first mention with `description` as an appositive (appended as an entrance sentence if the action never names them).
5. Dialogue: `{Name} says in a {delivery} voice, "{line}"`.
6. Ending: `The shot ends with {end_state}.` (or "…holds for the rest of the shot" when `end_state` restates the opening state).
7. Narration on the beat's first clip only, in the same paragraph, as an unseen voice; skipped when the clip has dialogue (pending Prompt 04).
8. A word count guard: warn above 200 words; the opening clip target 150–200, continuing 80–120.

**Client (`src/i2v/ltxv.py`):** send `continuity_frames` snapped to 8k+1; a derived seed when reproducibility is wanted; no negative prompt on the distilled variant; a `trim_overlap: true` flag; request `num_frames = snap(clip_seconds*fps) + overlap` so the delivered clip is exactly `clip_seconds`.

**Server (`server/ltx2_server.py`):** when the window was used and `trim_overlap` is set, drop the first `continuity_frames` decoded frames before encoding and report `X-Trimmed-Frames`; keep the window from the *untrimmed* tail as now; ignore `negative_prompt` on `distilled` with a log line; warm up on a neutral sentence; log the prompt's word count per turn. Optional later: hand the window over as latents instead of pixels (E10).

**Director prompt (`src/prompts/director.system.md`), generic:** the three renderer rules of §7.4 and a voice-over budget of about two words per second of clip.

**Blueprint (`stories/lego-void/story.json`), story-specific:** `visual_style` as positive caption prose; `visual_anchor` as one clause; `look_short` invariants only; the face rule stays; a size/shape rule for props that could be read as figures ("no minifigure parts") if the world rule matters visually.

---

## 10. Experiment matrix, prioritised

All on the GPU box with `LTX2_VARIANT=distilled`, fp8 + compile, 640×352, 145 frames, derived seeds so arms share noise. "Chain" = 12 consecutive clips from the same reference and the same Director outputs (the 2026-09-18 transcript's per-clip fields can be replayed offline through both compilers, so the LLM is out of the loop).

| # | Question | Arms | Control | Outputs | Success |
|---|---|---|---|---|---|
| E1 | Does trimming the overlap remove the seam hold without a jump? | trim 9 (render 153) vs today | same chain, same seeds | frame-diff profile across seams; ffprobe durations; a viewer watching the concatenation | overlap diff gone; no diff spike at the new seam above intra-clip motion; clips exactly 6.000 s |
| E2 | Does caption prose beat labelled fragments? | today's compiler vs §9 compiler | same chain × 3 seeds | figure bounding-box drift per clip; face-print check (frames 0/72/144, by eye or a vision judge with a framing-aware rubric); action-legibility rating; audio RMS per clip | prose arm equal or better on identity and framing with fewer words; actions still legible |
| E3 | Which anchor words hold identity and framing? | none / style clause only / identity invariants only / both | E2's prose compiler, same chain | brow/grin appearance clip index; floor greying; framing drift | choose the smallest arm with no drift by clip 12 |
| E4 | Window size and strength versus steering and seams | frames 1 / 9 / 17 × strength 1.0 / 0.9, one steering line injected at clip 4 | E1 trimming on for strength 1.0, crossfade for 0.9 | seam flow jump; clip index where the steered action starts; identity | pick the pair with the earliest landing and no seam jump |
| E5 | Seeds | random / fixed / derived, same chain | — | pairwise clip similarity; audio noise similarity; reproducibility of reruns | derived reproduces; fixed shows no repeated texture (else reject fixed) |
| E6 | What does the official enhancer make of our prompts? | offline: `gemma-4-E2B-it` with the I2V system prompt on 20 (compiled prompt, last frame) pairs from 2026-09-18 | render the enhancer output and the §9 prompt with the same seeds | side-by-side clips; enhancer latency on the box; a diff of phrasing | adopt phrasings that render better; decide runtime use only if latency < 1 s (unlikely) |
| E7 | Voice-over behaviour | same clip with / without the narrator line; lines of 8 / 12 / 20 words | — | speech present (RMS), speech duration by silence detection, face changes in frames | a words-per-second budget for the Director; whether narration moves the face |
| E8 | Physical-plausibility rule in the Director prompt | rule on / off, 10 scripted "wild" viewer lines | same seeds | artefact count by eye; validator-free | fewer artefacts with the rule, actions still honoured |
| E9 | Style LoRA feasibility | load a small LoRA (any 2.5-compatible one) on the server with fp8 + compile | bf16 eager | load success, per-clip time delta, output sanity | decide whether a trained style LoRA is even runnable before training one |
| E10 | Latent handoff versus pixel round trip | keep the last 2 latent frames vs re-encode 9 frames | E1 | `conditions` stage time; seam diff | adopt if faster and not worse |

---

## 11. Citations

### Repository (commit `1de34a0`)

- `src/story/compiler.py` — `describe` 48-60, `DESCRIBED_IN_FULL` 63, `compile_clip` 66-127: ambient 85-86, framing/camera 87-89, on-screen 91-97, opening pose 101-105, action 106-107, dialogue 108-113, end state 114-115, voice-over 118-121, style 123-127.
- `src/story/schemas.py` — `DialogueLine` 55-58, `ClipSpec` 61-72, `BeatPlan` 81-92.
- `src/story/context.py` — `format_cast` 23-34.
- `src/story/world.py` — `Entity` fields 31-52 (`look_short` 37, `status`/`holder` 38-40, state 50-52), `render_look`/`now` 54-59.
- `src/models/story.py` — `Character` 21-28, `Story.visual_anchor` 34.
- `src/prompts/director.system.md` — clip fields 48-56, discipline 64-69, no-cut rule 68.
- `src/graph/director_engine.py` — `clip_seconds` 93-94, `emit` compile call 250-262, duration 263.
- `src/i2v/ltxv.py` — defaults 39-43, continuity/anchor 68-74, negative/seed 84-85, `seconds_to_frames` 143-145, payload 177-191, `snap_to_frame_grid` 233-235, `letterbox_reference` 255-280.
- `src/eval/runner.py` — render calls 130-135, 440-442, 596-606.
- `src/web/player.html` — 250 ms crossfade 188-191.
- `server/ltx2_server.py` — recipe 224-247, placement 266-273, compile flags 457-467, warm-up 482-495, `GenerateRequest` 157-171, conditions 604-623, seed 625-628, call kwargs 636-648, window 677-679, encode 707-734, 847-871.
- `src/models/config.py` — LTX fields 47-70; `configs/director.live.yaml` 20-34.
- `stories/lego-void/story.json` — `visual_style`, `visual_anchor`, `look`, `look_short`, `reference_pose`, constraints.
- `tests/test_story.py` 171-211, 270-279; `tests/test_i2v.py` 47-93.
- Logs: `logs/live-box/director-ltxv_live_20260918_145552.md`, `logs/live-box/sessions/director-ltxv_live_20260918_145552/events.jsonl`, `logs/live-box/video/turn_*_20260918_*.mp4`.
- Earlier reviews: `docs/reviews/2026-09-15-substrate-sweep.md`, `2026-09-16-real-time-plan.md`, `2026-09-17-continuity-and-buffering.md` (§4 probe), `01_architecture_feature_review.md` (§4.12-4.13, §12).

### Primary sources (all accessed 2026-09-22)

- LTX-2.5 Prompt Guide, Lightricks blog, R. Luxemburg, 2026-08-10 — https://ltx.io/blog/ltx-2-5-prompt-guide
- Prompting Guide, LTX documentation — https://docs.ltx.io/open-source-model/usage-guides/prompting-guide
- Prompting guide for LTX-2, Lightricks blog, 2025-12-11 (what works / what to avoid) — https://ltx.io/model/model-blog/prompting-guide-for-ltx-2
- Model card, Lightricks/LTX-2.5 — https://huggingface.co/Lightricks/LTX-2.5
- Model card, Lightricks/LTX-2.5-Diffusers — https://huggingface.co/Lightricks/LTX-2.5-Diffusers
- LTX-2 repository README — https://github.com/Lightricks/LTX-2/blob/main/README.md
- ltx-pipelines docs: `pipelines.md`, `pipeline-selection.md`, `conditioning.md`, `multimodal-guidance.md` — https://github.com/Lightricks/LTX-2/tree/main/packages/ltx-pipelines/docs
- ltx-pipelines `utils/constants.py` (sigmas, CRF, default negative prompt, `DEFAULT_LORA_STRENGTH`) and `utils/helpers.py` (conditioning helpers) — https://github.com/Lightricks/LTX-2/tree/main/packages/ltx-pipelines/src/ltx_pipelines/utils
- ltx-core enhancer system prompts — https://github.com/Lightricks/LTX-2/tree/main/packages/ltx-core/src/ltx_core/text_encoders/gemma/encoders/prompts
- ltx-trainer `docs/training-modes.md` — https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-trainer/docs/training-modes.md
- diffusers `pipelines/ltx2/pipeline_ltx2_condition.py`, `pipeline_ltx2.py`, `utils.py` (main) — https://github.com/huggingface/diffusers/tree/main/src/diffusers/pipelines/ltx2
- diffusers LTX-2 API docs — https://huggingface.co/docs/diffusers/api/pipelines/ltx2
- Model card, Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients — https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients
- Model card, Lightricks/LTX-2.5-22b-IC-LoRA-Pixel-Spatial-Upscaler — https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Pixel-Spatial-Upscaler

Not used as evidence: third-party prompting guides (Runware, fal, community blogs). The 2026-09-17 investigation cited Runware's guide as "Lightricks' own guidance"; the official guides above are what this review relies on.

---

## 12. Discrepancies between the notes and the evidence

| # | Where | Note says | Evidence | Confidence |
|---|---|---|---|---|
| 1 | Audit §5.54; `configs/director.live.yaml:33`; `config.py:61-62` | The negative prompt "removes the green-face artifact" and is "sent with every clip" | It is sent and never encoded: no effect at `guidance_scale=1.0` (§2.5). The artefact fix belonged to the pre-2026-09-09 CFG-3 recipe | High [C] |
| 2 | Audit §5.22; CLAUDE.md | "At strength 1.0, the new clip is fully conditioned on the previous tail" | Only the first two latent frames (9 pixel frames, 10.5% of tokens) are conditioned; the rest is free generation guided by text | High [C] |
| 3 | Nowhere noted | — | Every clip's first nine frames duplicate the previous clip's last nine, motionless, plus a 250 ms crossfade (§3.3) | High [M] |
| 4 | `server/ltx2_server.py` docstring, README, 2026-09-15 review §3 | `LTX2_OFFLOAD=gpu` "drops the prompt enhancer (10 GB)" | The Diffusers pack does not ship `prompt_enhancer`; it loads as `None` (§2.6). Nothing was resident to drop on the current pack | Medium (depends on the pack revision used on 2026-09-15) [F][C] |
| 5 | `server/README.md` "Not wired yet" | Ingredients IC-LoRA "works on 2.5 at LoRA strength ~1.3" | The card: 1.4, on 2.3-dev, 30 steps, CFG 4, 768×448 × 121, with the general "most 2.3 adapters run on 2.5" caveat. No 2.5-specific figure | Medium [F] |
| 6 | CLAUDE.md "Current constraints" | "the compiler returns 6 whatever the shots add up to" | The compiler has no duration; `DirectorEngine.clip_seconds` sets it (§2.7) | High [C] |
| 7 | `docs/ARCHITECTURE.md` §67 | The compiler writes "in the order LTX-2.5 responds to (sound, camera, who is on screen, …)" | No official source describes that order; the official order puts audio last and integrated, camera as prose (§2.2-2.3) | High [F] |
| 8 | 2026-09-17 investigation §4 | "Lightricks' own guidance … says a focused brief beats an exhaustive one" (citing Runware), and 18–30-word prompts rendered actions fine | The model card says the model "degrades on short prompts"; the enhancer targets 150–220 words; the probe's own chain drifted by clip six. The direction (cut fixed text) is right; the length floor is higher than that probe suggests | Medium [F][M] |
| 9 | Audit §9.7 | Ambient sound and action "influence what audio is generated" | True, and incomplete: audio has no continuity across clips and no guidance on distilled (§2.10) | High [C][M] |
| 10 | Audit §13.5; 01 review §7 | A failed render skips the clip | Also leaves the window one clip behind the world (§3.4) | High [C] |
| 11 | Audit §4.5 | Dialogue as `Name says (delivery): "line"` | Form is close to official; the unaddressed problem is multiple voices per clip (§7.5) | Medium |
| 12 | `README.md` (LTX-2 repo) vs 2.5 guide | "Keep within 200 words" vs "match length to complexity, 4–8 sentences" | Both official; the newer guide is looser; the enhancer's 150–220-word target is the concrete number | — |
| 13 | Audit §9.4 | "Seed: default is random per clip" | Correct; adding that a *fixed* `ltxv_seed` reuses identical noise on every clip (§8) | High [C] |
| 14 | Audit §5.51 | Repeating full looks "confuses the model" | Plausible and consistent with 2026-09-17, but the confusion measured there was framing/face drift *without* any anchor; the specific harm of repetition is E3's question | Low [H] |

Confidence key: High = code or measurement here; Medium = official source read today, some inference; Low = hypothesis.
