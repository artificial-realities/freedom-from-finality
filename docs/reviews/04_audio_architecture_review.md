# 04 — Audio architecture review

*2026-09-22. Investigation and design only: no product code, prompt, schema, configuration or test was changed, no GPU was rented and no ElevenLabs request was made. Repository at commit `1de34a0`; the offline suite passes (25 of 25). Local work: the 63 clip files of the 2026-09-18 live run were analysed for loudness, level edges, speech content, speaker identity and the audio over the duplicated seam frames, and the repository's own `mux_audio_into_video` / `concat_videos_and_mux_audio` were run on synthetic signals. Scripts and derived data are in `logs/_scratch/review-04/` (gitignored; the tools run in a local venv there: faster-whisper `small.en`, Resemblyzer, librosa, ffmpeg 4.4.2).*

**Evidence labels.** **[F]** official source (Lightricks, diffusers, ElevenLabs docs, LTX-2 paper). **[C]** derived from reading code. **[M]** measured here. **[H]** hypothesis. **[E]** needs an experiment (§9). Marketing-level claims are marked as such where they are the only source.

**Run used.** Session A, `logs/live-box/director-ltxv_live_20260918_145552`: the only live run of the beat engine, `narration: "ltx"`, 64 segments, 63 clip files (segment 64 was not pulled), 16 voice-over lines, 15 of them in the pulled clips. No run in `logs/` has ever used TTS: there is no MP3 and no `*_muxed.mp4` or `*_span.mp4` anywhere [M].

---

## Executive summary

1. **LTX-2.5 already speaks the narrator's lines, verbatim and inside the clip.** All 15 narrated clips of session A contain the exact line (ASR word recall 1.00 in 14, 0.92 in one where the only miss is "standoff" transcribed as "stand-off"). The model fits the line to the 6 s clip. Speech ends between 4.8 and 6.0 s, and the speaking rate stretches from 1.3 words/s for a 7-word line to 3.2 words/s for the 19-word opening line. No line was truncated or spilled [M]. The fear that motivated spanning ("~25 words, 8–10 s against 6 s", reviews 02 and 03) rests on a miscount: the lines average **10.9 words / 71 characters**. The 149 characters reported earlier are the whole compiled narrator sentence, wrapper included (§11.3).
2. **The LTX narrator sounds like one voice at the resolution available here.** Speaker-embedding similarity between narrated clips has a median of 0.831, the same as the within-clip baseline of 0.828 (two halves of the same utterance). Median pitch per clip stays between 104 and 152 Hz [M]. This does not prove one identity. A listening test decides it (E1), and that test is the only thing still arguing for external TTS narration.
3. **The audio seams are made by the model, then made audible by the player.** (a) Every clip's audio starts from fresh noise, and over the nine duplicated frames the new clip's audio is no more similar to the old clip's tail than two random clips are (spectral cosine 0.89 vs 0.88) [M][C]. (b) Clips decay to digital silence before they end: 37 of 63 end with ≥ 0.2 s below −60 dBFS (median 0.62 s, p90 3.9 s). Then the next clip starts abruptly, with a first-sample amplitude ≥ 0.05 in 12 of 63 [M]. (c) The median level step across a seam is 36 dB (100 ms windows) [M]. (d) Narrated clips sit a median **20.8 LU** above the clip that follows, and 13 of 15 peak above −1 dBTP (one at +0.1) [M]. (e) The player does not crossfade; it fades the new clip in from silence while the old one has already gone quiet (`src/web/player.html:186-191`) [C].
4. **Audio spanning costs more than it buys.** It exists for lines longer than one clip, which at 10.9 words per line and a 6 s clip almost never occur (of session A's 16 lines only the 19-word opener would exceed 6 s at a TTS pace of 2.5–3 words/s [H]). Each span forces the serial render path (`runner.py:585-620`). It withholds the narrated clip until the next clip is rendered and the pair re-encoded, and it misleads the JIT scheduler. The estimated cost is a stall of about 0–4 s per spanning line, and up to one render (4.8 s) of added steering latency while serial renders run [C][H]. The TTS mux itself has two defects: the bed sits at −18 dB under the line and then jumps +6 dB the instant the line ends, and a line longer than the clip is cut off in the single-clip path [M].
5. **Decision.** LTX owns everything that happens inside a clip: ambience, foley, footsteps, on-screen dialogue and, by default, narration (option 1 of §4). The player owns everything between clips: the crossfade over the nine duplicated frames, a story-supplied room-tone floor and, only if the product wants it, a per-scene music bed. The server adds a loudness ceiling and a true-peak limiter at encode time. Remove audio spanning, the serial path and `audio_seconds_owed` now. Keep TTS narration only if E1 shows the LTX narrator's identity drifts. If kept, rebuild it as a parallel, never-spanning path (§6).
6. **None of this serializes video generation.** The crossfade runs in the player on clips that are already delivered. The loudness pass is a few milliseconds of NumPy inside the existing encode. Music and room tone are generated off the critical path, per scene or per story.

---

## 1. Current audio behaviour, in plain language

### 1.1 What a viewer hears today [C][M]

Each 6 s clip arrives from the LTX-2.5 server as an MP4 with a 48 kHz stereo AAC track the model generated together with the picture (`server/ltx2_server.py:684-711`). The track contains what the prompt describes. The Director writes an `ambient_sound` phrase for every clip, and in session A every one of them was a variant of "soft plastic clicks" (all 63 clips) [M]. On the first clip of a beat the track can also carry the narrator's line, when the voice-over gate was open and the Director wrote one. The film's soundtrack is therefore a chain of 6 s islands: plastic foley at mostly −25 to −43 LUFS, one narrated island about every four clips at −9 to −14 LUFS, and between islands, often, a stretch of digital silence. The web player switches `<video>` elements at each seam and ramps the new element's volume from 0 to 1 over 250 ms. It also shows the voice-over text as a caption (`player.html:44-45, 157-159`).

### 1.2 Narration modes and defaults [C]

| Setting | Where | Default | Shipped configs | Effect |
|---|---|---|---|---|
| `narration` | `config.py:84` | `"ltx"` | `ltx` in all three | `ltx`: line folded into the LTX prompt of the beat's first clip (`compiler.py:118-121`). `tts`: the compiler leaves it out, and ElevenLabs renders it if `audio_enabled` (`director_engine.py:267-281`). `off`: the gate is always closed (`pacing.py:19-20`) and the Director is told narration is not part of the film (`context.py:244-245`) |
| `audio_enabled` | `config.py:76` | `false` | `false` | Builds the ElevenLabs client (`runner.py:64-71`). It is used only when `narration: "tts"` too |
| `elevenlabs_voice_id` | `config.py:77` | `""` | unset | Without it TTS logs a warning and returns `None` (`elevenlabs.py:44-46`) |
| `min_pause_seconds` | `config.py:81` | `5.0` | `15.0` | The silence floor of the gate |
| `voiceover_style` | `config.py:85` | "warm, unhurried documentary narrator" | default | The voice description in the LTX prompt. A run-config field, not a blueprint field |

Voice-over is therefore **gated, and present by default**. The Director may write a line whenever the gate is open, in every mode except `off`, and headless runs produce it as text. In `tts` mode without `audio_enabled` the line becomes a caption only.

### 1.3 The voice-over gate [C]

`voiceover_gate` (`pacing.py:17-25`) is evaluated once per Director call, i.e. once per beat (`director_engine.py:137`). It returns false when narration is off, false while `audio_seconds_owed > 0`, **true unconditionally while no history entry has ever carried a line** (the first-line rule), and otherwise `silence_seconds ≥ min_pause_seconds`. When it is closed the Director is told so (`context.py:251-252`), and whatever it writes anyway is cleared (`director_engine.py:151-152`). A line rides on the beat's first clip only (`:264`).

- **Silence tracking.** Live, `silence_seconds` starts at the floor so the first beat may speak (`runner.py:409-411`). It is reset to 0 on a clip that carries a line and grows by the clip's *planned* duration on every other clip at emit time (`_pace_expected`, `runner.py:856-866`). On the serial path it grows by the *measured* duration after render (`:736-738`). Headless, `tick_story_clock` does the same per segment (`pacing.py:28-40`). The narrated clip's own 6 s are never counted, so with a 15 s floor and two-clip beats the gate reopens on the third beat after a line. Session A: 16 lines in 33 beats, one every 4–5 clips [M].
- **Audio owed.** Only a TTS span sets it (`runner.py:713`, `:663`), which keeps the gate shut until the line has finished playing. In `ltx` mode it is always 0.
- **First-line behaviour.** Live, the rule is redundant because silence starts at the floor. Headless, it is what opens the gate on segment 1. Either way the film opens with a line if the Director writes one, and in session A it did.
- **Resumed-session history.** `StoryState.history` is not persisted, so on a resume the first-line rule would fire again. Review 01 established that resume is unreachable from `main.py`: every run creates a fresh session directory (`runner.py:51-53`) and the load branch in `_init_world` (`director_engine.py:323-326`) never runs. The issue is moot, and the rule is removed in §8 anyway.

### 1.4 Voice-over versus dialogue [C]

They are separate channels. **Voice-over** is `BeatPlan.voiceover`: one narrator line per beat, spoken by nobody on screen, gated by pacing and never by `speaks`. **Dialogue** is `ClipSpec.dialogue`: lines spoken by a character (`speaker` entity id, `line`, `delivery`) in a specific clip, not gated by pacing (`schemas.py:55-72, 87`).

- **Can characters speak today?** Yes, in any story whose blueprint leaves `speaks: true`, which is the default (`models/story.py:25`). The compiler renders each line as `Name says (delivery): "line"` in the LTX prompt (`compiler.py:108-113`), so LTX voices it together with the picture. Dialogue has never been rendered in a logged run, because the only story's only character is mute.
- **Mute enforcement.** `speaks: false` from the blueprint (`world.py:46, 177`) produces a hard violation, which triggers the one retry (`validator.py:98-103`). The line is also filtered out before the compiler reads it (`:226-229`), and the cast sheet says "Never speaks" (`context.py:31-32`). It never fired in session A (01). It covers *dialogue lines* only. Non-verbal vocal sounds from a mute figure are not addressed by any rule. Clips 37 and 38 of session A contain short events Whisper transcribed as "Whoo!" and "Oof!" with no-speech probabilities of 0.56 and 0.70. The spectrogram of 37 shows ten evenly spaced taps (its action is a bounce) and a harmonic burst in its last 0.6 s that may be a vocalisation [M][H].
- **Narration and dialogue in one clip.** Nothing prevents it. On a beat's first clip the compiler emits every dialogue line *and* the narrator paragraph (review 02 §6.7), i.e. up to three voices in 6 s.

### 1.5 TTS muxing, ducking and spanning [C][M]

In `tts` mode, `emit` awaits `ElevenLabsTTS.synthesize` (`eleven_multilingual_v2`, non-streaming `convert`, `mp3_44100_128`; `elevenlabs.py:54-68`) *after* the JIT loop has decided it is time to fire the render (`director_engine.py:267-281`). The TTS time is thus added to the narrated clip's render start. The clip then takes the serial path (`runner.py:609-620`), and the audio is handled in one of two ways:

- **Fits in the clip** (≤ clip + 0.05 s): `mux_audio_into_video` mixes it over the native track with `volume=0.25` on the bed and `amix duration=first:dropout_transition=0` (`mux.py:88-110`). Measured with synthetic signals through the repository's own function (ffmpeg 4.4.2): the bed sits at **−18.1 dB** under the line (the 0.25 gain plus amix's default 1/N normalisation) and **jumps to −12.1 dB, with no ramp, the moment the line ends** (renormalisation with `dropout_transition=0`). The line itself plays at **−6.0 dB** relative to the TTS file. The duck is static: it covers the whole clip, not just the speech [M].
- **Longer than the clip**: a span opens (`runner.py:702-721`). The narrated clip is *withheld from the queue*. Every following clip is rendered serially, appended and paid down against `owed` (`:660-692`). When owed reaches zero, `concat_videos_and_mux_audio` re-encodes the video of all clips (no `-c:v copy`, `mux.py:216-230`), ducks the concatenated bed and enqueues one long item. Beds are all-or-nothing: if any clip lacks audio, all native audio is dropped (`:196-214`). The span continues across beat boundaries and viewer interrupts: a line from the old beat keeps playing over the clip that answers the viewer.
- **`run_play`** (terminal loop) muxes inline and never spans (`runner.py:150-158`), so a 9 s line on a 6 s clip is **cut at 6.016 s** (measured, `amix duration=first`). The terminal loop does not play the file.

### 1.6 Why spanning bypasses the pipelined render, and what it costs [C][H]

The pipelined path requires `not state.current_audio_path and pending_span is None` (`runner.py:585-589`). The reason is code structure, not a model dependency. The background task `_bg_render_and_enqueue` (`:434-455`) enqueues the clip itself and knows nothing about spans, while the span bookkeeping needs the rendered path synchronously in the producer (`:654-741`). The render never depends on the audio (audit §16.3 is right about that).

Cost, derived from session A's timings (render 4.80 s per clip, a beat's first clip ready 5.9–6.1 s before air, JIT fire 9.5 s before air; review 03 §1.3):

- **A narrated clip that fits.** The render starts after the TTS call, and the slack of about 4.7 s absorbs a TTS time below about 4 s. No stall is expected. During the serial render the producer does not listen for input, so a line typed then reaches the Director up to 4.8 s later, and its answer beat can slip one clip.
- **A spanning line.** Clip *k* (narrated) is rendered and withheld. For clip *k+1*, `_needed_at` counts only queued clips (`:480-488`), so it believes *k+1* is due when *k* was and fires at once. *k+1* is then ready about 1.2 s before *k*'s air time, plus the TTS time. Then the concat re-encode runs. If *k+1* opens a new beat, a Director call (mean 2.98 s) comes first. **Estimated stall: max(0, t_TTS + t_concat − 1.2 s), plus 3 s when a Director call intervenes, so roughly 0–4 s per spanning line.** Neither t_TTS nor t_concat has ever been measured (no TTS run exists), hence [H].

### 1.7 Where the audio seams come from [C][M]

| # | Source | Where | Frequency (session A) |
|---|---|---|---|
| S1 | Audio latents start from fresh noise every clip; only video frames are carried in the window | `pipeline_ltx2_condition.py:1235-1265` (diffusers), `ltx2_server.py:675-679` | every seam; the new clip's audio over the nine duplicated frames is unrelated to the old clip's audio over the same frames: spectral cosine 0.89 vs 0.88 for random pairs, level difference 30.7 vs 34.4 dB [M] |
| S2 | The model lets the clip's sound decay to digital silence, sooner when the prompt says "then silence" | model behaviour | 37/63 clips end with ≥ 0.2 s below −60 dBFS (median 0.62 s, p90 3.9 s); 1.35 s median when the ambient phrase mentions silence/quiet/still, 0.26 s otherwise [M] |
| S3 | Abrupt onset: the next clip starts mid-waveform | model output, no fade | first sample ≥ 0.05 (≈ −26 dBFS step) in 12/63 clips [M] |
| S4 | Level step between the end of one clip and the start of the next | S1–S3 combined | median 36 dB, p90 57 dB, ≥ 20 dB at 41/62 seams (100 ms windows) [M] |
| S5 | Narrated clips are much louder and near full scale | the voice dominates the clip's loudness | narrated median −10.4 LUFS vs −32.3; narrated minus next clip median 20.8 LU; 13/15 narrated clips above −1 dBTP [M] |
| S6 | Container gap: audio 6.010 s vs video 6.042 s | `ltx2_server.py:863` (`-shortest`) | 32 ms per clip (review 03 3c) |
| S7 | No crossfade: the new element ramps 0→1 over 250 ms while the old one's audio has already ended (S2, S6) | `player.html:186-191, 196-205` | every seam |
| S8 | Picture overlap: the new clip's first 375 ms repeat the old clip's last 375 ms, but with newly generated audio | `continuity_frames: 9` | every seam (02 §3.3) |
| S9 | TTS mode only: static duck, +6 dB bed step at line end, concat re-encode | `mux.py:94-110, 199-230` | every narrated clip in `tts` mode [M] |

---

## 2. Current audio data flow

```
Director (BeatPlan)                 clips[].ambient_sound, clips[].dialogue, voiceover (beat)
   │  gate closed → voiceover cleared                    director_engine.py:137-152
   ▼
validator.apply_clip                drops dialogue of mute / unknown speakers   validator.py:226-229
   ▼
compile_clip                        ambient sentence → … → dialogue "Name says (d): …" → end state
   │                                + narrator paragraph on clip 1 if narration == "ltx"    compiler.py:85-121
   ├──────────────── narration == "tts" and audio_enabled ────────────────┐
   ▼                                                                       ▼
LTX-2.5 server (distilled, audio guidance 1.0)                 ElevenLabs convert (multilingual v2, MP3)
   frames + 48 kHz stereo audio from fresh noise                   awaited inside emit, before the render
   ffmpeg: x264 + AAC 192k, -shortest                              elevenlabs.py:54-68
   ▼                                                                       │
client (ltxv.py) → clip.mp4                                               │
   ├── no TTS audio, no open span → pipelined: enqueue as is               │
   └── TTS audio or open span → serial path ◄─────────────────────────────┘
          fits:   mux (bed ×0.25, amix) → enqueue                          mux.py:58-143
          longer: open span; withhold; render next clips serially;
                  concat + re-encode + mux → enqueue one long item          runner.py:654-741, mux.py:146-248
   ▼
queue → consumer → WebPlayer SSE → <video> A/B: volume 0→1 over 250 ms, caption = voiceover text
```

---

## 3. Verified capabilities and constraints

### 3.1 LTX-2.5 native audio

| Claim | Status | Source |
|---|---|---|
| Joint audio-video generation; "synchronized, high-fidelity video and audio" | marketing-level; the model card gives no audio specification | [F] HF model card |
| Asymmetric dual-stream DiT (14B video, 5B audio in LTX-2) with bidirectional A/V cross-attention; audio latents from 16 kHz mel spectrograms, one token ≈ 1/25 s; HiFi-GAN-style stereo vocoder at 24 kHz | [F] | LTX-2 paper §3 |
| LTX-2.5 in diffusers ships `LTX2VocoderWithBWE` (bandwidth extension); the server encodes 48 kHz, and clips measure 48 kHz stereo | [C][M] | diffusers `vocoder.py:479`; ffprobe |
| Speech in quotation marks; language and accent in prose; off-screen speakers cued in prose; describe at each cut "whether music / dialogue / ambience continues or changes"; in Dub-It, "prompt too long: the model might skip words; too short: slow and unnatural" | [F] | LTX prompting guide |
| Known limits: weaker speech in under-represented languages; in multi-speaker scenes "the model may inconsistently assign spoken content to characters"; drift beyond ~20 s | [F] | LTX-2 paper §7 |
| On the distilled recipe audio guidance is 1.0, so the prompt is the only lever on audio | [C] | `ltx2_server.py:229-236`; diffusers `:1319` |
| No audio continuity: the diffusers condition pipeline takes `audio_latents` only as a whole-track noised init, with no per-token audio conditioning mask | [C] | diffusers `pipeline_ltx2_condition.py:1235-1265, 1734-1757` |
| Partial-audio conditioning exists in the official `ltx-pipelines` code: `RetakePipeline` keeps audio and video outside a `TemporalRegionMask` and regenerates inside it, including on the distilled model; `A2VidPipelineTwoStage` denoises video with audio frozen | [F][C] | `retake.py:52-75, 271-282`; `a2vid_two_stage.py:58-66` |
| A2Vid (audio in, lip-synced video out) runs the **dev** transformer, two-stage, with the distilled LoRA; it is not in diffusers | [F] | LTX-2 README (pipelines, checkpoints) |

The last two rows matter for the design. Carrying the previous clip's audio tail into the next render (true ambience continuity) is architecturally supported, but not on the pipeline this server uses. It would mean porting the sliding window to `ltx-pipelines` (E10, later). External-audio-driven lip sync costs the full model's speed: 3.5× the distilled time on 2026-09-15.

### 3.2 Measured on session A [M]

| Property | Result | Method |
|---|---|---|
| Narrator line rendered | 15/15, word recall 1.00 (one 0.92 by hyphenation) | faster-whisper `small.en`, word timestamps, order-preserving match (`asr.py`) |
| Fit to the clip | speech ends 4.82–5.98 s; rate 1.29–3.18 words/s (median 2.2), inversely tracking line length | same |
| Narrator identity | cross-clip speaker cosine median 0.831 (p10 0.770, min 0.703) vs within-clip halves 0.828 (0.674–0.911); median F0 104–152 Hz | Resemblyzer, librosa pYIN (`voice.py`) |
| Unprompted speech in unnarrated clips | none credible: the only transcripts are Whisper's known non-speech hallucinations ("You", "Thanks for watching!", no-speech probability 0.62–0.83); 37/38 ambiguous (§1.4) | same |
| Loudness | narrated −13.6 to −8.9 LUFS (median −10.4); unnarrated −43.5 to −12.6 (median −32.3); true peak up to +0.1 dBTP | ffmpeg `ebur128=peak=true` (`loud.py`) |
| Edges | see S2–S4 in §1.7 | 10 ms envelopes (`edges.py`) |
| Overlap audio | unrelated to the previous clip's audio over the same frames | log-mel cosine and level, real seams vs 293 random pairs (`overlap.py`) |
| Does narration change the picture? | no sign of it. Seven frames of each narrated clip against fifteen unnarrated controls: the one striking artefact in a narrated clip (31: the lying figure replaced for ~3 s by a standing, camera-facing figure) also occurs in controls 21, 30 and 34. Global frame-difference jumps: 2/15 narrated vs 16/48 unnarrated | `cuts.py`, `vo_*.png`, `ctl_a.png` |

The mid-clip reversion to a standing, front-facing pose is a visual issue for the prompting and continuity reviews, not an audio one. It is recorded here because it answers review 02's E7 question ("whether narration moves the face") in the negative for this run.

### 3.3 ElevenLabs [F]

| Capability | Documented | Caveat |
|---|---|---|
| TTS models | Flash v2.5 `eleven_flash_v2_5`, "ultra-low latency (~75 ms†)", 32 languages, 50% lower price per character; Multilingual v2 (the repo's model and the API default) "most stable on long-form", no latency figure; v3 expressive, not real time; v3 Conversational "~280 ms" | the latency figures are model-side (the dagger); network and full-file time are not stated |
| Voice consistency | a fixed `voice_id`; `seed` ("repeated requests with the same seed should return the same result"); `previous_text` / `next_text` and request stitching "to improve the speech's continuity"; `voice_settings.speed` | consistency is by construction (same voice), not measured here |
| Music | Eleven Music: instrumental or vocal, section-by-section composition plans, streaming endpoint `POST /v1/music/stream`, `force_instrumental`, `music_length_ms` 3 000–600 000 (the capability page says a 5-minute maximum), API for paid subscribers | licensing "cleared for nearly all commercial uses" is a marketing summary; the binding terms are model-specific and plan-dependent. No looping or continuation is documented |
| Sound effects | 0.1–30 s, seamless looping option, 40 credits per second | no latency statement |

### 3.4 What is not known

- Whether a listener hears the LTX narrator as *one person* across a film (E1).
- TTS latency from this client (p50/p95) and the concat re-encode time on the box (E8).
- Whether LTX adds music or vocalisations on its own (a sustained tonal component is visible under narrated clip 31) [H] (E9).
- How characters' voices hold across clips in a story with dialogue (E6); no such story exists yet.

---

## 4. Ownership options

| | 1. LTX owns all scene audio, incl. dialogue and narration | 2. LTX scene audio; TTS narration | 3. As 2, plus external music | 4. External tools own most audio |
|---|---|---|---|---|
| **Synchronisation** | exact by construction: foley, footsteps, lip movement and the line are generated together [M] | scene audio exact; narration is off-screen, so it needs only to start at the right clip | as 2; music needs only scene-level sync | lost: SFX made without the frames will not land on the click; fixing it needs a video-to-audio model after the render |
| **Voice consistency** | narrator re-sampled every clip from text; stable at embedding resolution [M]; identity unproven (E1); characters untested | narrator exact (one `voice_id`); characters still LTX | as 2 | exact for every voice, but characters lose lip sync unless A2Vid (dev model, 3.5× slower) |
| **Cinematic control** | prompt only (audio guidance off); loudness and mix are whatever the clip is | narration level, timing and ducking controllable | adds score, a strong mood lever and the best seam masker | full, at the cost of everything else |
| **Latency** | +0 | +TTS time before render today; +0 if synthesized in parallel (§6) | music generated per scene off the critical path | +V2A or SFX generation after each render: serial, seconds per clip |
| **Cost** | none beyond the GPU | ~10–11 k characters per hour of film at session A's density (16 lines × 71 chars per 6.4 min) | + one music cue per scene (≈ 15 per hour at 240 s scenes), plan-dependent | per-clip SFX (40 credits/s) plus TTS plus music |
| **Failure modes** | a line mispronounced or rushed (19 words in 6 s ran at 3.2 w/s); line lost if the render fails; voice drift | API error → caption only (fail-soft exists); line belongs to a beat the viewer just cut; spans today | cue mood wrong after steering; licensing; double music if LTX adds its own | everything in 2 and 3, plus sync failure |
| **Mixing complexity** | none on the server; player crossfade only | a mux per narrated clip, or a player-side narration track | a continuous bed in the player with ducking automation | a full mixer |
| **Clip duration** | a line must fit one clip: about 2.2 × `clip_seconds` words (13 at 6 s, 9 at 4 s) | TTS line length independent of the clip only with a player-side track; otherwise spans or truncation | music independent | independent |

**Verdict.** Option 1 is the only one that keeps every sound synchronised at zero added latency, and the measurements remove its main supposed weakness (lines spilling past the clip). Its open risk is narrator identity, which E1 tests with existing clips and no GPU. Option 2 is the fallback if E1 fails, rebuilt as in §6. Option 3's music is a product choice, not a technical necessity: the seams are fixed by §7 without it. Option 4 is rejected; it gives up the one thing joint generation does that nothing else can.

---

## 5. Recommended target architecture by category

| Category | Owner | How | Notes |
|---|---|---|---|
| Character dialogue | **LTX** | `Name says in a … voice, "line"` in the clip where it is spoken (02 §7.5); per-character `voice` from the blueprint on every speaking clip | TTS + lip sync needs A2Vid on the dev model, not real time; one voice per clip (below) |
| Vocalisations (effort sounds, laughs) | **LTX** | allowed for speaking characters; for `speaks: false` the compiler adds "he makes no vocal sound" [H] | test with E9 before relying on it |
| Ambience | **LTX** per clip, plus a **story room-tone floor** in the player | the Director's `ambient_sound`, woven into the action (02 §7.6) | the room tone (§7.4) turns S2's digital-silence holes into silence |
| SFX / foley / footsteps | **LTX** | tied to the verb that makes the sound ("…with a soft plastic click") | clip 37 ("taps as he bounces") carries ten evenly spaced taps; frame-accurate sync was not measured |
| Narration | **LTX by default**; TTS only if E1 fails | unseen narrator in the same paragraph (02 §7.5), voice and language from the blueprint; ≤ ~2 words per second of clip; never on a clip with dialogue | the blueprint decides whether a film has a narrator at all (§10) |
| Background music | **none by default**; optionally the **player**, per scene | Eleven Music instrumental cue per scene card, requested asynchronously when a card lands; crossfaded 3 s at scene changes; ducked under speech | LTX prompts then say "no music" [H]; licence per plan |
| Silence | **the Director and the gate** | quiet clips say "the studio is silent apart from …"; loudness never boosted; room tone underneath | silence is room tone, not digital zero |
| Mixing and loudness | **server** (per clip) and **player** (between clips) | ceiling + true-peak limiter at encode; crossfade, room tone, music and ducking in the player | nothing waits on anything else |
| Transitions | **player** | equal-power crossfade over the nine duplicated frames (§7.1) | uses audio the server already sends |

**Multiple voices.** One voice per clip: either the narrator or one character. A two-character exchange becomes consecutive clips, one speaker each. The total spoken words in a clip stay within ≈ 2 × `clip_seconds`. This follows the LTX-2 paper's speaker-assignment limitation and the measured time-fitting. No design here relies on overlapping voices, crowd dialogue or narrator-over-dialogue. Crowd murmur is ambience, not dialogue.

---

## 6. Keep, simplify or remove

| Mechanism | Recommendation | Reason |
|---|---|---|
| **Voice-over as a capability** | **Keep**, story-configurable | Measured to render verbatim inside the clip at no cost. Whether a film has a narrator, its voice, language and density are story properties. They move to the blueprint, which also resolves the Lego contradiction (§10) |
| **The gate** | **Simplify**: `narrator enabled ∧ silence ≥ floor`; silence starts at the floor in every mode | `audio_seconds_owed` exists only for spans; the first-line rule is redundant live and history-based (resume-fragile) |
| **Audio spanning** | **Remove** (`pending_span`, `concat_videos_and_mux_audio`, `audio_seconds_owed`) | Rare at 10.9-word lines; forces serial renders, withholds a ready clip, misleads JIT, stalls ~0–4 s [H], re-encodes video, carries a cut beat's line over the viewer's answer |
| **Serial render path** | **Remove**; one pipelined path for every clip | it only exists to serve TTS and spans |
| **TTS muxing** | **Simplify now, then decide by E1.** If kept: synthesize in parallel with the render inside the background task (the render does not depend on it), request Flash v2.5 with `seed`, mux after both finish with gain automation (bed −10 dB only while the line plays, 150 ms ramps, `amix normalize=0`), and never exceed the clip: if the line is longer, re-request with `speed` up to 1.15, else show it as a caption only. If E1 passes: **delete** `src/tts/`, the mux module and `audio_enabled` | today's path adds TTS time to the render start, ducks the whole clip, steps the bed +6 dB at line end [M], and has never run |
| **Captions** | **Keep** (the player already shows `voiceover`) | accessibility, and the fallback for any lost line |

---

## 7. Transition and mixing design

### 7.1 Crossfade policy between clips

The nine conditioning frames make every seam an overlap. Clip *N+1*'s first nine frames (0.375 s) *are* clip *N*'s last nine, so *N+1*'s first 0.375 s of audio is a second, independent rendition of a moment already on screen [C][M]. That window is where the crossfade belongs, and it needs no extra audio.

1. **Overlap, not append.** The player starts clip *N+1* when *N* reaches its frame 136 (`duration − 9/24 s`). The picture switches at that instant with no opacity fade: the frames are the same, and review 03 measured the current opacity crossfade dimming the picture by 24%. *N+1*'s video plays from its frame 0, which is identical to what *N* was about to show. The duplicate hold disappears without any server change.
2. **Equal-power crossfade over the 375 ms.** *N* fades out with cos(π t / 2T) and *N+1* fades in with sin(π t / 2T), T = 0.375 s. Equal-power, not equal-gain, because the two signals are uncorrelated (measured, §3.2). When *N* is already silent in that window (33 of 62 seams in session A), this reduces to a 375 ms fade-in of *N+1*, which also removes S3's onset click.
3. **Speech-safe variant.** When *N* carries a spoken line (non-empty `voiceover` or `dialogue` on that clip), *N* stays at unity through the window and gets only a 20 ms fade at its end, while *N+1* fades in beneath it. Speech never fades out mid-word: five of fifteen narrated clips speak into their last 0.375 s (ends at 5.78–5.98 s) [M]. When *N+1* carries a line, it gets a 30 ms fade-in and *N* yields over the full window, so the first syllable is not swallowed. The gate's floor makes both at once impossible for narration. For dialogue, the one-voice-per-clip rule makes it rare, and the incoming line wins.
4. **The first clip of a run** (seeded from one reference frame): a 30 ms fade-in, no overlap.
5. **The story clock.** Because clips now overlap by 9 frames, each clip's hold becomes `delivered_frames − 9` frames (6.000 s for a 153-frame render, review 03's target). The server-side hold must use the same number, or the player drifts 0.375 s per clip ahead of the producer.

**Relation to review 03.** Review 03 proposed trimming the overlap on the server, audio included. This review refines that: keep the overlap in the file and let the player overlap it, because trimming the audio server-side leaves nothing to crossfade over. A server cannot pre-mix the crossfade into *N* either, since *N* is already on air when *N+1* is rendered. Pre-mixing would add a clip of latency.

**Implementation stages.** (a) With the two `<video>` elements: start the standby element on time and ramp both volumes. `requestAnimationFrame` gives about 16 ms resolution, adequate for a 375 ms curve, while element start jitter (1–25 ms, 03) is below audibility for uncorrelated ambience. (b) With 03's MSE player: appending *N+1* with a timestamp offset of `end(N) − 0.375 s` overwrites *N*'s duplicate frames exactly (*N+1* opens on a keyframe). Audio cannot overlap inside one `SourceBuffer`, so it moves to Web Audio: each clip's track is decoded to an `AudioBuffer`, started with `AudioBufferSourceNode.start(when)` on the `AudioContext` clock, faded with `setValueCurveAtTime`. The video then slaves to that clock.

### 7.2 Duplicated frames and audio trim

- Do not trim audio on the server. Do pad it to the video length (`apad` before `-shortest`, 03 Q3) so the file is exactly A/V aligned and S6's 32 ms hole goes away.
- Render 153 frames (6.375 s) so that, after the 9-frame overlap, each clip contributes exactly 6.000 s (03 §8.1). One warmed shape, as today.
- If the conditioning window changes (03's R5 tests 1, 9 and 17 frames), the crossfade length follows: T = window / 24 s. A 1-frame window leaves a 42 ms crossfade, still enough to kill clicks.

### 7.3 Loudness

Normalising every clip to a common loudness would lift the quiet clips' noise to narration level and erase intentional silence. The rule is a **ceiling, never a boost**:

- In `_encode_mp4`, before the WAV is written, measure the clip's integrated loudness (ITU-R BS.1770 K-weighting on the in-memory waveform, a few milliseconds of NumPy) and apply a static gain `min(0, L_ceiling − I)` with `L_ceiling = −18 LUFS`. Then limit true peak to −1 dBTP (`alimiter` in the same ffmpeg call, or a NumPy look-ahead limiter). Report `X-Loudness-LUFS` and `X-True-Peak` headers.
- On session A this lowers every narrated clip by 4.4–9.1 dB and leaves all unnarrated clips unchanged except clip 37 (−12.6 → −18). The narrated-to-next step falls from a median of 20.8 LU to 13.7 LU (range 3.7–18.1; computed from the measured values), which is the ordinary gap between speech and quiet foley. It also removes all 14 true-peak overs.
- The ceiling is a server constant, not a story field, until a second story shows it needs one.

### 7.4 Ambience continuity: a room-tone floor

LTX cannot carry ambience across clips on this pipeline (§3.1), and 37 of 63 clips end in digital silence. The player mixes a continuous, quiet room-tone loop (a story asset, e.g. `stories/lego-void/room_tone.ogg`, 20–30 s, seamless) at about −50 LUFS under everything. It turns holes into silence and gives every crossfade a common bed. It is generated once per story, offline (an ElevenLabs looping sound effect, a library recording, or a 20 s LTX clip of the empty set), and is optional: no asset, no floor. Whether −50 LUFS reads as "silence" or as hiss on laptop speakers is E4.

### 7.5 Music bed (only if chosen, §10)

- One instrumental cue per scene card, requested from the music API when the Showrunner lands a card. That is the slow clock, off the critical path. A card is used by the next Director call, about 14 s before that beat airs (03 §1.2), and the stream endpoint returns audio as it is generated, so a cue can usually start with its scene; when it is late, the previous cue continues and the new one crossfades in on arrival [H].
- The player loops or extends the current cue until the next arrives, then crossfades over 3 s at the first beat of the new scene. Cue length ≈ the scene's intended length (`scene_seconds`, 240 s).
- It ducks −8 dB under any clip with a spoken line (known from `Playable`), 200 ms attack, 500 ms release, via gain automation. No sidechain analysis is needed because the player knows which clips speak.
- While music is on, the compiler adds "no music" to the LTX prompt so the two scores do not collide [H]. With no cue available (API failure), the room tone alone continues.
- A viewer's steering can change the mood faster than the scene clock. Whether a cue should react to an absorbed line is a product decision (§10); the simple rule is "no, until the next scene".

### 7.6 Narration ducking and dialogue priority

- LTX mode: nothing to duck. The line and its bed come from one generation, and the ceiling (§7.3) tames the level.
- TTS mode (if kept): gain automation on the clip's native bed, −10 dB from the line's start to its end + 150 ms, then back to unity with a 300 ms ramp. `amix normalize=0`, so neither input is halved and nothing steps when the line ends.
- Priority order in the player: dialogue/narration > foley/ambience (the clip) > music > room tone. Only music is ducked; the clip already contains its own balance.

### 7.7 Failure fallbacks

| Failure | Behaviour |
|---|---|
| Clip has no audio stream | play it; the room tone continues; crossfade treats the missing side as silence |
| Clip render fails | nothing new: the player holds the last frame (existing behaviour); room tone continues; a lost narrated line stays in the transcript and the caption |
| Loudness measurement fails | encode without gain; the limiter still runs |
| TTS fails or times out (if kept) | caption only, never blocks the render (it is already fail-soft, `elevenlabs.py:48-52`) |
| Music API fails | keep the current cue or none; room tone continues |
| Browser blocks audio until a gesture | existing overlay (`player.html`); Web Audio contexts resume on the same gesture |

### 7.8 Nothing here serializes video generation

The server's only new work is a per-clip gain and limiter inside the encode it already does. Every cross-clip operation happens in the player on clips that were rendered and delivered on the existing schedule. Music and room tone are fetched on scene or story timescales. TTS, if kept, runs concurrently with the render. With spanning and the serial path removed, every clip follows the one pipelined path.

---

## 8. Required changes (proposals only, none made)

**Blueprint (`src/models/story.py`, story JSON; story-specific data, generic mechanism)**
- `narrator` (optional object): `voice` (e.g. "a warm, unhurried older male documentary narrator"), `language` (default `"English"`), `min_pause_seconds` (default 15), and the register, which today is `tone_guidelines`. No `narrator` means no narration, and the gate stays closed. This replaces `voiceover_style` in the run config and the hard-coded "English" in `compiler.py:120`.
- `Character.voice` (review 02's A1) for speaking characters.
- `room_tone` (optional asset path); `music` (optional: `enabled`, `style` prompt for cues).
- Lego story: either add a `narrator` and delete "no voiceover" from world constraint 4, or remove the narrator register. That is a product decision (§10).

**Director schema (`src/story/schemas.py`)**: no change. `voiceover`, `dialogue` and `ambient_sound` stay as they are. Structure is enforced in the validator and compiler, per the project rule.

**Director prompt (`src/prompts/director.system.md`, generic)**
- Voice budget: "Spoken words in one clip, voice-over or dialogue, stay under about two per second of clip." The template already has `{clip_seconds}`.
- "One voice per clip: no voice-over on a clip that has dialogue, and one speaker per clip."
- Keep "empty is the normal choice", and let the blueprint's floor carry density. 01 found the Director fills 16 of about 17 open gates.

**Validator (`src/story/validator.py`)**: warnings, not violations, for (a) spoken words over budget (drop the voice-over first, then trailing dialogue lines), and (b) more than one speaker in a clip (keep the first speaker's lines). Voice-over is dropped on a clip with dialogue. The mute check stays as is.

**Compiler (`src/story/compiler.py`)**: narrator as an unseen voice in the same paragraph, with voice and language from the blueprint (02 §5 row 8); dialogue with `Character.voice`; for mute characters "he makes no vocal sound" [H, E9]; "no music" when the story has a music bed; quiet clips close with "the studio is silent apart from …" (02 §7.6).

**Engine and runner**
- `pacing.voiceover_gate`: `narrator is not None and silence ≥ floor`; drop `audio_seconds_owed` and the history scan. Silence starts at the floor in `run_play` too.
- `runner.run_live`: delete `pending_span`, the serial branch and `_render_turn(mux_inline=False)`. Every rendered clip goes through `_bg_render_and_enqueue`. If TTS stays, that task gathers `render` and `tts` concurrently, then mixes.
- `director_engine.emit`: move TTS out of `emit` (it is on the render's start today, `:267-281`).
- `Playable`: add `speech` (`"none" | "narration" | "dialogue"`) for the player's crossfade and ducking rules, and `overlap_seconds` (9/24 except on a run's first clip).
- Hold: `delivered_frames − overlap_frames` over 24 fps, shared by the producer's clock and the player (with 03's frame-based hold).

**Server (`server/ltx2_server.py`)**: in `_encode_mp4`, pad audio to the video length, apply the loudness ceiling and the −1 dBTP limit, and add `X-Loudness-LUFS` / `X-True-Peak` headers. Keep the overlap frames in the file.

**Player (`src/web/player.html`)**: overlapped start at `duration − overlap`, no opacity crossfade, the equal-power audio crossfade with the speech-safe variant, the optional room-tone loop, and (with MSE, 03 Q11) audio through Web Audio.

**Config (`src/models/config.py`, `configs/*.yaml`)**: `narration: "ltx" | "tts" | "off"` stays as the *rendering* choice. Whether a film has a narrator moves to the blueprint, so rendering flags no longer gate content (CLAUDE.md rule). `min_pause_seconds` moves to the blueprint, with its code default aligned to the 15 s every config uses. `voiceover_style` is deleted. `audio_enabled` is either renamed `tts_enabled` or deleted with TTS after E1. If TTS stays: `elevenlabs_model: "eleven_flash_v2_5"` and `elevenlabs_seed`.

**Tests**: gate truth table without owed; the compiler drops narration on a dialogue clip; the word-budget warning; the loudness function on a synthetic loud and quiet clip (ceiling applied, quiet untouched, true peak ≤ −1 dBTP); `Playable.speech` set correctly.

---

## 9. Evaluation plan

### 9.1 Objective metrics (automatable; promote `logs/_scratch/review-04/*.py` to `scripts/audio_check.py`)

| Metric | Today (session A) | Gate |
|---|---|---|
| Narration word recall (ASR) | 15/15 ≥ 0.92 | ≥ 0.95 for ≥ 95% of lines |
| Narration rate | 1.3–3.2 w/s | 1.8–2.8 w/s for ≥ 90% of lines (the word budget should get there) |
| Narrator identity: cross-clip speaker cosine vs within-clip baseline | 0.831 vs 0.828; min 0.703 | median within 0.03 of baseline; no pair < 0.70 |
| Clip loudness above ceiling | 16/63 above −18 LUFS | 0 |
| True peak > −1 dBTP | 14/63 | 0 |
| Narrated-to-next loudness step | median 20.8 LU | ≤ 14 LU median |
| Digital-silence holes in the *played mix* (≥ 200 ms below −70 dBFS) | 37 clips end in one | 0 per minute (room tone on) |
| Onset clicks (first 5 ms step ≥ −30 dBFS after mixing) | 12/63 | 0 |
| Seam level step, 100 ms each side, in the played mix | median 36 dB | ≤ 12 dB median, ≤ 25 dB p90 |
| Stall count / held seconds; steering latency | 03's baseline | unchanged vs video-only (TTS mode must add 0 stalls) |
| Voice rule | never exercised | 0 clips with two voices (text) and speaker cosine consistent per character (E6) |

"Played mix" means the offline reproduction of what the player outputs: the same crossfade, room tone and ducking rendered with ffmpeg from the clip files. Browser audio cannot be captured headlessly with confidence. The browser's *timing* is checked with the existing headless-Chromium harness (03).

### 9.2 Human listening

- **H1 narrator identity** (decides E1). 20 pairs of narrated clips drawn from one run, plus 5 same-clip control pairs, rated blind: "the same narrator?" Passes if ≥ 90% of cross-clip pairs are judged "same" and controls score ≥ 95%.
- **H2 seams.** Three-minute excerpts in four conditions (today; crossfade + ceiling; + room tone; + music if pursued), order randomised, rated 1–5 for "noticeable joins in the sound" and overall preference. At least three listeners, headphones and laptop speakers.
- **H3 silence.** In the room-tone condition: "did the sound cut out at any point?" (count), and "does the quiet sound like a room or like hiss?"
- **H4 dialogue** (after E6/E7): "who is speaking?" per clip, and "is that the same voice as before?" per character.

### 9.3 Experiments

| # | Question | Needs | Decides |
|---|---|---|---|
| E1 | Is the LTX narrator one voice to a listener? | session A clips, H1 | keep or delete TTS |
| E2 | Does the overlap crossfade remove the seam? | offline mix of 20 seams + player timing in headless Chromium | §7.1 rollout |
| E3 | Ceiling and limiter on all 63 clips | offline; §9.1 metrics; H2 | ceiling value (−18 vs −20 LUFS) |
| E4 | Room tone level and material | offline mix, H3 | the floor level, per-story asset |
| E5 | Line length vs rate and recall | GPU, one clip, lines of 6/10/14/18 words, 3 seeds (02's E7) | the words-per-second budget |
| E6 | Character voice consistency | GPU, synthetic two-character story, 10 speaking clips per character with and without `Character.voice` | whether `voice` is enough |
| E7 | Narrator + dialogue in one clip vs split | GPU, 10 clips each | confirms the one-voice rule |
| E8 | TTS path cost (only if E1 fails) | ElevenLabs Flash v2.5 vs Multilingual v2, t_TTS p50/p95 from the box, 64-clip run, stall count | TTS configuration |
| E9 | Unprompted music and vocalisations; "no vocal sound" clause | GPU, 20 clips with/without the clause; tonal-component and ASR checks | compiler clauses |
| E10 | Audio continuity through a temporal audio mask | port of the window to `ltx-pipelines` (Retake-style mask on the audio tail) | research, after the MVP |

---

## 10. Open product decisions

1. **Does the Lego film have a narrator?** World constraint 4 says "no dialogue, no voiceover"; `tone_guidelines` defines an Attenborough narrator; the system follows the latter (16 lines in 64 clips).
2. **Narrator density.** One line every 4–5 clips (24–30 s) is what a 15 s floor produces. Is that the register, or should the floor be 30–45 s (01 Q5)?
3. **"A consistent documentary voice" or "this exact voice"?** The first is what LTX measurably gives; the second needs TTS and brings back a mux path. E1 answers whether the first is good enough.
4. **Music.** None (default), a per-scene instrumental bed, or later. If yes: which plan's licence covers the intended use (streaming, public demo, commercial)?
5. **Room tone** as a per-story asset: acceptable, or should silence stay digital?
6. **Captions** for narration: on by default (today), or a viewer toggle?
7. **Dialogue pace.** One speaker per clip makes a two-line exchange last 12 s at 6 s clips. Accept, or shorten clips for dialogue scenes (03's R4)?
8. **Mute characters' non-verbal sounds** (grunts, "whoo"): allowed, or suppressed in the prompt?
9. **Languages.** English only, or a blueprint `language` per story (and per character)?

---

## 11. Citations, sources and discrepancies

### 11.1 Repository

- Gate and clock: `src/story/pacing.py` — `voiceover_gate` 17-25, `tick_story_clock` 28-40. `src/story/context.py` — cast "Never speaks" 31-32, `format_gates` 241-253.
- Engine: `src/graph/director_engine.py` — gate 137, clear 151-152, voice-over on first clip 264, TTS in `emit` 267-281, resume branch 323-326.
- Compiler: `src/story/compiler.py` — ambient 85-86, dialogue 108-113, narrator paragraph 118-121 (hard-coded "English" 120).
- Schemas: `src/story/schemas.py` — `DialogueLine` 55-58, `ClipSpec` 61-72, `BeatPlan.voiceover` 87. Blueprint: `src/models/story.py:25`. World: `src/story/world.py:46, 131, 177`. Validator: `src/story/validator.py:98-103, 226-229`.
- Runner: `src/eval/runner.py` — `_maybe_tts` 64-71, inline mux 150-158, pacing init 409-411, `_pipeline_ok` 417, `_bg_render_and_enqueue` 434-455, `_needed_at` 480-488, span state 462-467, path choice 585-620, render-failure span abort 631-652, span and mux handling 654-741, `_pace_expected` 856-866.
- Audio: `src/tts/elevenlabs.py:40-68`; `src/playback/mux.py` — `AMBIENT_DUCK_GAIN` 25, ambient amix 88-110, silent branch 111-125, all-or-nothing beds 196-214, concat re-encode 216-230.
- Server: `server/ltx2_server.py` — distilled defaults incl. audio guidance 229-236, sample rate 290-292, audio tensor 684-693, encode 837-871 (`-shortest` 863). Client: `src/i2v/ltxv.py:218-229`.
- Player: `src/web/player.html` — caption 44-45 and 157-159, element switch and fades 165-205. Terminal: `src/playback/player.py:139-151`.
- Config: `src/models/config.py:76-85`; `configs/director.live.yaml:36-41`; `configs/director.yaml:12-16`.
- Blueprint: `stories/lego-void/story.json` — `world_constraints[3]` versus `tone_guidelines`.
- Tests touching narration: `tests/test_engine.py:57-59`, `tests/test_web.py:171`.

### 11.2 Primary sources (read 2026-09-22)

- LTX-2 paper, arXiv 2601.03233 — §3 (audio VAE, vocoder, cross-attention), §5.1 (captions with dialogue, speaker, language), §7 (limitations): https://arxiv.org/abs/2601.03233
- LTX-2 repository README (pipelines, checkpoints; A2Vid on the dev model, Retake, Dub-It): https://github.com/Lightricks/LTX-2/blob/main/README.md
- `A2VidPipelineTwoStage`: https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-pipelines/src/ltx_pipelines/a2vid_two_stage.py
- `RetakePipeline` (temporal region mask on audio and video, distilled mode): https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-pipelines/src/ltx_pipelines/retake.py
- diffusers `pipeline_ltx2_condition.py` and `vocoder.py` (main, 2026-09-22): https://github.com/huggingface/diffusers/tree/main/src/diffusers/pipelines/ltx2
- LTX-2.5 model card (marketing-level audio claim only): https://huggingface.co/Lightricks/LTX-2.5
- LTX prompting guide (audio, dialogue, continuity at cuts, Dub-It timing): https://docs.ltx.io/open-source-model/usage-guides/prompting-guide
- ElevenLabs models: https://elevenlabs.io/docs/overview/models
- ElevenLabs text-to-speech API (seed, stitching, speed, defaults): https://elevenlabs.io/docs/api-reference/text-to-speech/convert
- Eleven Music: https://elevenlabs.io/docs/capabilities/music ; stream API: https://elevenlabs.io/docs/api-reference/music/stream ; terms: https://elevenlabs.io/music-terms
- ElevenLabs sound effects: https://elevenlabs.io/docs/capabilities/sound-effects
- Loudness: ITU-R BS.1770 (the measurement ffmpeg's `ebur128` implements); EBU R 128: https://tech.ebu.ch/docs/r/r128.pdf
- Web Audio API (sample-accurate `start(when)`, `setValueCurveAtTime`): https://www.w3.org/TR/webaudio/

### 11.3 Discrepancies

| # | Where | Claim | Finding | Confidence |
|---|---|---|---|---|
| 1 | Review 02 §4.1/§4.3, carried into 03 §4 | voice-over lines average 149 characters (~25 words), 8–10 s of speech against 6 s | lines average **71 characters / 10.9 words** (max 19); 149 ≈ the compiled sentence with its 78-character wrapper | High [M] |
| 2 | Review 02 §4.3 item 10, E7 | whether the model rushes, truncates or spills is untested | tested on session A: no truncation or spill; rate adapts to length (1.3–3.2 w/s) | High [M] |
| 3 | Review 03 root cause 3c | the audio hole at each clip end is 32 ms | the larger hole is the model's own decay: 37/63 clips end with ≥ 0.2 s of digital silence (median 0.62 s) | High [M] |
| 4 | Review 03 §8.1 | trim the overlap and the same duration of audio on the server | the overlap audio is the only material the crossfade can use; overlap in the player instead (§7.1) | Medium (design) |
| 5 | Audit §5.24 | ambient ducked to 25% under the voice-over | −18 dB during the line (amix halves both inputs), then +6 dB instantly when the line ends; the line itself at −6 dB | High [M] |
| 6 | Audit §5.25 | without spanning the audio would be cut off or padded | the single-clip mux cuts it (verified at 6.016 s); only the live loop spans | High [M] |
| 7 | Audit §15 (2021-2023) | audio spanning forces *every* clip to render serially | serial for every clip with TTS audio plus every clip while a span is open; spans would be rare at today's line length | High [C] |
| 8 | Audit §5.38 | TTS gives "a fixed narrator voice with consistent quality" | voice fixed by `voice_id`, yes; quality never observed: no TTS run exists in `logs/` | High [M] |
| 9 | `src/tts/elevenlabs.py:3` | "Only invoked when `audio_enabled: true`" | also requires `narration: "tts"` (`director_engine.py:268-273`); `_maybe_tts` builds a client whenever `audio_enabled` | High [C] |
| 10 | `src/playback/mux.py:83-85` | `-shortest` keeps a short audio from trimming the video | applies to the silent-clip branch only; the ambient branch uses `amix duration=first` | High [C] |
| 11 | `configs/director.live.yaml:37-38` | TTS for "a fixed voice" (implying LTX's is not) | LTX's narrator is stable at embedding resolution; identity for a listener is E1 | Medium [M] |
| 12 | `src/story/compiler.py:120`, `config.py:85` | — | story-specific voice data (language, narrator voice) in code and run config, against the story-agnostic rule | High [C] |
| 13 | Audit §9.7 | with `narration: "ltx"` the model generates the narration | confirmed, verbatim, 15/15 | High [M] |
| 14 | Audit §16.1 | resumed runs may produce an unwanted opening line | unreachable (01); the proposed gate removes the history dependency anyway | High [C] |
| 15 | Review 02 §2.10 | "the vocoder outputs 48 kHz" | the LTX-2 paper's vocoder is 24 kHz; LTX-2.5 in diffusers adds bandwidth extension, and the clips measure 48 kHz; consistent, but the 48 kHz is the BWE output | Medium [F][M] |
| 16 | ElevenLabs music docs | maximum length 5 minutes (capability page) vs 600 000 ms (stream API) | the two official pages disagree; check before relying on long cues | Medium [F] |

---

## Appendix: how the numbers were obtained

All scripts are in `logs/_scratch/review-04/` and read only session A's files.

- **Loudness** (`loud.py`): ffmpeg `ebur128=peak=true` per clip for integrated LUFS and true peak; mono 48 kHz PCM for RMS and first/last-sample amplitude.
- **Edges** (`edges.py`): 10 ms RMS envelopes; trailing silence from the last window above −60 dBFS; seam step = first 100 ms of *N+1* minus last 100 ms of *N*, in dB.
- **Speech** (`asr.py`): faster-whisper `small.en` on CPU (int8, beam 5, word timestamps, no VAD); word recall is the order-preserving match (`difflib`) of normalised words against the logged line. Whisper word times can start early on quiet onsets (clip 31's spectrogram shows speech from 1.8 s while the first word is stamped 0.0), so the rates are lower bounds for lines with a late start.
- **Identity** (`voice.py`): Resemblyzer utterance embeddings of each narrated clip's speech span (16 kHz); within-clip baseline from the two halves of the same span; pYIN median F0. Short utterances (2–6 s) make these embeddings noisy, which is why the baseline is reported next to the cross-clip figure.
- **Overlap** (`overlap.py`): the last 0.375 s of *N* (in video time) against the first 0.375 s of *N+1*: RMS difference and cosine of mean-removed 40-band log-mel spectra; baseline from 293 random non-adjacent pairs.
- **Picture checks** (`cuts.py`, `vo_a.png`, `vo_b.png`, `ctl_a.png`): consecutive-frame mean absolute difference at 160×90 over frames 9–144; seven frames per clip for all 15 narrated clips and 15 unnarrated controls, inspected by eye.
- **Mux** (`mux/run.py`): a 6.042 s clip with a 440 Hz bed and a 3 s or 9 s 1 kHz "line", run through `src.playback.mux` unchanged; band levels by FFT.
- **Ambient phrases** (`prompts.py`): parsed from the session transcript's compiled prompts.
