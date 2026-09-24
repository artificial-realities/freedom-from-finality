# 03 — Runtime, clip duration and interactivity review

*2026-09-22. Investigation and experimental design only: no product code, configuration or test was changed, and no GPU was rented. Repository at commit `1de34a0`; the offline suite passes (25 of 25). Local work: the three box runs were re-read from their clip files and Langfuse traces (read-only), and the real `WebPlayer` and `player.html` were driven in headless Chromium with replayed clips to measure the seam. Scripts and derived data are in `logs/_scratch/review-03/` (gitignored).*

**Evidence labels.** **[M]** measured here on this repository's logs, clips or a local run. **[C]** derived from reading code. **[F]** official source (only where 02 already cited it). **[H]** hypothesis. **[E]** needs an experiment (§11).

**Runs used.**

| Name | Log | Engine | Clips | Notes |
|---|---|---|---|---|
| **Session A** | `logs/live-box/director-ltxv_live_20260918_145552` | beat engine, JIT, commit `265e9fd` | 67 on air per Langfuse (63 files pulled; the pull ran before the film ended) | the only run of the current runtime; H100 box (the `up` log for that day was not kept; render times match the NVL of 09-16 within 0.1 s) |
| Session V | `…_20260916_213519` | per-clip Director, no JIT | 10 | the "verified live" run of the real-time plan; H100 NVL |
| Session B | `…_20260917_143416` | per-clip Director, sync Showrunner joins | 29 | the run behind the "1–2 s between clips" report |

---

## Executive summary

1. **The one-to-two-second seams are gone from the server side.** They were queue starvation under the old per-clip engine (session B: 16 of 28 transitions held, 45 s in total). In session A the consumer never waited: 0 of 66 transitions, every clip handed over within 1 ms of the previous clip's hold [M]. What a viewer still sees at every seam is produced after the queue: nine duplicated frames (375 ms), a crossfade that dims the picture by about 24% for roughly 100 ms, an audio track that stops 32 ms early and then fades in from silence, and, on some seams, a source reload during or at the seam [M][C]. All of these can be fixed without touching the model.
2. **The canonical render number is 4.80 s per 6.04 s clip (0.794×), p95 0.802×, max 0.820×**, measured client-side on the box over 62 warm clips of session A [M]. The "0.76×" in the notes is a different metric: server-side generate+encode on 09-16 (4.0 + 0.6 s). The B200's "0.76×" is one 5 s clip at 768×416. The spread is small (sd 46 ms), but the wall time crept up by 2.2 ms per clip over the run, which a 30-minute film would have to confirm is bounded.
3. **`lead_clips: 1` buffers only at startup.** The just-in-time schedule ignores `lead_clips`, so after the fourth clip the lead collapses to `0.25 × render + 0.5 + render_slack` ≈ 4.7 s. It never regains a full clip (measured 4.71–6.09 s from clip 5 on) [M][C]. Steering lands at N+2 or N+3 depending on when in the clip the line arrives (first ~2.6 s → N+2), with either lead setting. Measured line-to-steered-clip-on-air: 10.9, 11.1, 12.5 and 14.6 s [M]; the model predicts 9.5–15.5 s.
4. **The real stall risk is the viewer's own line, not the GPU.** A line that arrives just before a render's fire time holds that render until the Director returns, which spends the buffer. In session A the smallest margin was 1.87 s after a 4.37 s answer call [M]. A validator retry or an API tail of 6 s or more would stall the picture. The fix is deadline-aware admission: a line targets the earliest clip whose render can still fire on time *after* the Director's p95.
5. **Duration: one fixed, warmed shape, owned by a single runtime object.** Today the length is derived in five places from two config fields, with three different values in play (6 s nominal, 6.0417 s of frames, 6.042 s of container). Keep 6 s for now. Delete `i2v_duration` and make the delivered frame count, not an integer of seconds, the unit every consumer reads. A duration menu or model-predicted length is not justified yet: each costs a compile per shape (90–170 s under fp8) or an untested dynamic compile, and nothing measured shows it improves pacing.
6. **Beats, not longer clips, are the right way to make moments last.** A longer clip adds up to its own length to steering latency. More clips per beat add nothing to it, because unrendered clips are dropped when the viewer speaks. Keep the cap at 4. Each clip in a beat costs about 1.1 s of Director time (1 clip 1.75 s, 2 clips 2.94 s, 3 clips 3.95 s [M]), which only matters when the call answers a line. Answer beats should be capped at two clips.
7. **True seamlessness needs one media timeline.** Two `<video>` elements switched on a server event cannot join frames or audio samples exactly. The smallest change that can is a Media Source Extensions player that appends each clip as a fragmented-MP4 segment to one `SourceBuffer`, together with overlap trimming on the server. The player fixes in §7 get close to seamless without it.
8. **Benchmark discipline is the missing piece.** No run records its hardware, code revision, render timings, input receipt time or seam timing. Every number in this review was reconstructed from file mtimes and Langfuse events. §9 specifies a `timing.jsonl` per run, a machine-readable baseline, and gates stated as tails (p95, max, stall count) instead of a mean ratio.

---

## 1. Current runtime model and timeline

### 1.1 Components and every wait point [C]

| Where | Wait | Code |
|---|---|---|
| Producer, `_listen` | JIT sleep until `fire_at`, woken early by viewer input | `src/eval/runner.py:516-554` (`wait_input` 534) |
| Producer, `_plan` | Director call, cancellable by a new line; one validator retry inside | `runner.py:490-514`; `src/agents/director.py` |
| Engine, first `plan()` | synchronous Showrunner bootstrap (4.5 s in session A) | `src/graph/director_engine.py:132-135` |
| Producer, `emit` | TTS synthesis (only `narration: "tts"`) | `director_engine.py:267-281` |
| Producer, `_drain_prev` | the previous render task: HTTP, file write, ffprobe, `queue.put` | `runner.py:471-478, 434-455` |
| Producer, serial path | render + mux/concat when TTS audio exists | `runner.py:609-741` |
| Queue | `put` blocks at `lead_clips + 1`; `get` blocks when empty (starvation) | `runner.py:391, 428, 777` |
| Consumer | lead gate at startup; `player.play` holds `item.duration` | `runner.py:758-796`; `src/web/server.py:99-106` |
| Client | `requests.post` in a thread, `REQUEST_TIMEOUT_S = 600`; re-seed on a 400 | `src/i2v/ltxv.py:165-230` |
| Server | `_lock` around the GPU work; encode outside the lock | `server/ltx2_server.py:523-534` |
| Browser | SSE delivery, `prefetch` on `queued`, element switch and 250 ms crossfade on `playing` | `src/web/player.html:165-230` |

The client never overlaps two requests: `_drain_prev` awaits the whole previous render before the next fires, so the server's "encode outside the lock" never overlaps a GPU call in practice [C].

### 1.2 The schedule [C]

Once playback runs, the render for clip *k* fires at

```
fire(k) = air(k) − (JIT_SAFETY · R̂ + JIT_MARGIN_S + render_slack_seconds)      runner.py:524
        = air(k) − (1.25 · 4.80 + 0.5 + 3.0) = air(k) − 9.5 s
```

where `air(k)` is when clip *k* will go on air: the start of the clip on air, plus its hold, plus the holds of queued clips, plus one nominal clip for a render in flight (`_needed_at`, `runner.py:480-488`). `R̂` and `D̂` are exponential averages with weight 0.5 (`_learn`, `runner.py:423-425`). When the beat has no clip left, the Director call starts earlier, at `fire(k) − 1.25·D̂ − 0.5` (`runner.py:529`). `lead_clips` appears nowhere in this computation. It sets only the queue size and the startup gate (`runner.py:379, 759-768`).

### 1.3 One cycle, measured (session A, steady state) [M]

Times relative to the moment clip *k* goes on air:

| t (s) | Event |
|---|---|
| −13.7 | if *k* opens a beat: Director call starts (`fire − 1.25·D̂ − 0.5`) |
| −10.8 | Director returns (mean 2.98 s) |
| −10.8 / −9.5 | render *k* fires: right after the Director for a beat's first clip, at `fire(k)` otherwise |
| −6.0 / −4.7 | render *k* done (4.80 s wall), enqueued, `queued` sent, browser prefetches |
| 0 | consumer takes *k*; `playing` sent; the browser switches elements within 1–25 ms; 250 ms crossfade |
| +6.04 | next clip |

The buffer ahead of the screen, measured as time from file write to on-air, was 4.71–4.80 s for mid-beat clips and 5.9–6.1 s for a beat's first clip, over clips 5–67. Startup was higher (6.1–8.7 s for clips 2–4). The minimum, 1.87 s at clip 35, came from a viewer line (§1.5). First picture: 17.9 s after the run started (Showrunner bootstrap 4.5 s, Director 3.7 s, two renders).

### 1.4 What lead does and does not do [M][C]

`lead_clips: 1` makes the consumer wait for two clips before starting. During that wait `_needed_at` returns `None`, so the producer runs flat out and renders clips 3 and 4 early. From then on every fire is JIT, and the margin settles at `0.25·R̂ + 0.5 + render_slack` ≈ 4.7 s (clip 4: 8.67 s; clip 5: 4.76 s; never above 6.1 s again). `lead_clips: 0` would reach the same steady state; only its first seconds are thinner. `CLAUDE.md` ("buffers one clip… which makes it about N+3") and the audit (§5.29, §10.12) describe the startup, not the film.

### 1.5 Viewer lines on the timeline [M][C]

A line arriving at time *t* lands on the first clip *k* whose `fire(k) ≥ t`. If the beat still has clips, the line cuts it and the Director is called at once (`runner.py:536-543`). The render of *k* then fires at `max(fire(k), t + D)`, so a line arriving less than *D* before `fire(k)` spends margin. Latency from the line to the steered clip on air lies in `[9.5, 9.5 + L)` s = 9.5–15.5 s for L = 6.04.

| Line (session A) | Arrived (≈ Director start) | Landed | Latency to on-air | Director | Margin of that clip |
|---|---|---|---|---|---|
| "start breakdancing" | 1.18 s into clip 18 | 20 (N+2) | 10.9 s | 3.55 s | 2.60 s |
| "take 100 pushups" | 5.68 s into clip 25 | 28 (N+3) | 12.5 s | 3.80 s | 3.88 s |
| "a monster shows up…" | 1.04 s into clip 33 | 35 (N+2) | 11.1 s | 4.37 s | **1.87 s** |
| "they start kissing…" | 3.53 s into clip 45 | 48 (N+3) | 14.6 s | 3.96 s | 4.71 s |

Receipt times are not logged. The Director start is a close lower bound, because the call starts as soon as `_listen` sees the line. The measured mean of 12.3 s matches the model's 12.5 s.

---

## 2. Canonical interpretation of the performance evidence

### 2.1 Three metrics hide behind "×RT" [M]

| Metric | Definition | Who measured it |
|---|---|---|
| server ×RT | (generate + encode) / clip length, from the server's headers | 2026-09-16 plan, both cards |
| wall ×RT (tunnel) | request to MP4 on disk, client on the laptop, through the ssh tunnel | `scripts/latency_probe.py` `ratio` |
| wall ×RT (box) | fire to file written, client on the box; what the scheduler lives by | this review, from file mtimes and turn spans |

### 2.2 All records, reconciled

| Record | Card, setting | Clip | n (warm) | Server ×RT | Wall ×RT | Status |
|---|---|---|---|---|---|---|
| probe `nvl-fp8-640-compile` | H100 NVL, fp8+compile, 640×352 | 5 s (121 f) | 3 | 0.79–0.83 | 1.09–1.20 (tunnel) | superseded by live data |
| probe `nvl-fp8-640-compile-8s` | same | 8 s (193 f) | 1 | 0.79 | 1.02 (tunnel) | single clip |
| probe `nvl-fp8-768-compile` | H100 NVL, 768×416 | 5 s | 3 | 1.15–1.18 | 1.51–1.59 | above RT |
| probe `b200-fp8-768-compile` | B200, 768×416 | 5 s | **1** | 0.74 (clips 1–2: 1.84, 1.50) | 0.98 (tunnel) | the B200 "0.76×": one clip after 3–6 s of unexplained work |
| probe `b200-fp8-960-compile` | B200, 960×544 | 5 s | 2 | 1.17–1.19 | 1.46–1.50 | above RT |
| session V (09-16) | H100 NVL, fp8+compile, 640×352 | 6 s (145 f) | 9 | 0.76 (4.0 + 0.6 s) | **0.776**, max 0.782 (box) | the "0.76×" of the notes |
| **session A (09-18)** | H100, same | 6 s (145 f) | **62** | not logged | **0.794 mean, 0.795 p50, 0.802 p95, 0.820 max** (box) | **canonical** |

**Canonical baseline**: LTX-2.5 distilled (8 sigmas, unguided), fp8 dynamic (torchao PerRow) + regional `torch.compile`, 640×352, 24 fps, 145 frames = 6.0417 s, `continuity_frames: 9` at strength 1.0, anchor off, libx264 ultrafast, client and server on one H100 box. Render wall per clip: **4.80 s mean, sd 0.046 s, p95 4.85 s, max 4.96 s** [M]. The prompt's working baseline is confirmed, except that 0.76× is the server-side ratio. The scheduler sees 0.79–0.82×.

### 2.3 Variance and trend [M]

- Render wall: range 0.26 s over 62 clips. Session V: 4.67–4.72 s. The render is the most predictable stage in the system.
- Trend: the first ten warm clips of session A averaged 4.73 s, the last ten 4.83 s (slope +2.2 ms per clip). Cause unknown: thermal state, allocator fragmentation or the client process [H]. If linear, a 300-clip film would end near 5.4 s (0.90×). This must be measured before any threshold is trusted for long runs (R8).
- Director (gpt-4.1, priority tier): n = 35, mean 2.98 s, sd 0.59, p95 3.80, max 4.37. Output tokens 206–669 (mean 384); latency tracks output length (r = 0.86 against output characters).
- Showrunner: 8 calls, mean 3.70 s, max 4.82; off the path except the bootstrap.
- First clips of a fresh box: CPU warm-up of 10–20 s on each of the first three clips (H100 PCIe, 09-15). On the B200, 3–6 s of untimed work on the first two clips after warm-up. The warm-up renders exist to absorb this; the B200 needs three per shape.

### 2.4 What is not known

The hardware of session A; any server-side stage timing for sessions A and B (they went to `server.log` on destroyed boxes); tail behaviour beyond 67 clips; box-to-box variance on the same offer type (09-15 PCIe versus 09-16 NVL differ by the card, not only the box).

---

## 3. Root-cause tree for buffering and seams

```
A viewer-visible seam or hold
├── 1. Queue starvation (consumer waits; last frame held + "buffering")
│   ├── 1a. Per-clip LLM on the critical path: Director 6.6 s > 6.04 s clip       session B: 16/28 held, 45 s   [M]  FIXED by beats
│   ├── 1b. Synchronous Showrunner joins (2.3–8.0 s)                              session B                     [M]  FIXED 09-17
│   ├── 1c. Viewer line near fire time: render waits for the Director             session A: margin → 1.87 s   [M]  OPEN (latent)
│   ├── 1d. Director validator retry or API tail (≥ 6 s) on a line                not observed (0 retries)      [C]  OPEN (latent)
│   ├── 1e. Render slower than the clip (R > L): renders back up                  not observed (max 0.82×)     [M]  guarded only by the margin
│   ├── 1f. A frame count that was not warmed: 90–170 s compile mid-film          not observed                  [C]  OPEN (no shape contract)
│   └── 1g. Failed render: clip skipped, world one clip ahead                     not observed                  [C]  OPEN
├── 2. Picture discontinuity at a seam that is on time
│   ├── 2a. Nine duplicated frames (the conditioning window re-rendered)          every seam, 375 ms            [M] (02 §3.3)
│   ├── 2b. Crossfade of two stacked layers over a black stage: −24% luminance    every seam, ~100 ms < −10%    [M] (this review)
│   ├── 2c. Source reloaded on `playing` because the prefetch was overwritten     queue ≥ 2 deep: startup (clips 1-4) in A, at most 2 later cases within 20 ms; every clip in the 09-17 replay  [M]
│   │        cost: 9–25 ms locally, 0.21–0.36 s through the tunnel               [M] (09-17 browser logs)
│   ├── 2d. Source replaced on the fading-out element (queued ≤ 0.25 s after `playing`)  15-18/62 seams in A  [M] mechanism; magnitude not separated from 2b
│   ├── 2e. Switch driven by the SSE event, not the media clock                   ±1 frame                      [C]
│   └── 2f. Hold = container duration 6.042 s vs 6.0417 s of video                +0.3 ms/clip                 [M] negligible
├── 3. Audio discontinuity
│   ├── 3a. Each clip's audio is generated from fresh noise: ambience restarts    every seam                    [C] (02 §2.10)
│   ├── 3b. Loudness jumps between narrated and silent clips, up to ~35 dB        narrated seams                [M] (02)
│   ├── 3c. Audio stream 6.010 s vs video 6.042 s: 32 ms of silence per clip end  every clip sampled            [M]
│   └── 3d. New clip's volume ramps 0→1 over 250 ms while the old audio has ended every seam                   [C]
└── 4. Not causes in session A
    ├── Video generation time (render done 4.7–6.1 s before air)                  [M]
    ├── Encode/mux (0.6 s, inside the render wall; mux only in TTS mode)          [M]
    └── HTTP serving and SSE (0–2 ms local, ~50 ms tunnel; clips prefetched)      [M] (09-17)
```

Details worth stating:

- **2b, measured.** Real clips from session A, the real `WebPlayer`, headless Chromium, screenshots every 34 ms over the centre of the picture. Mean luminance fell from 225 to 170, from 224 to 168 and from 211 to 159 at three seams, bottoming about 50 ms after `playing` and recovering by 200 ms (`logs/_scratch/review-03/dim-luminance.json`). The arithmetic explains it: both elements animate `opacity` over `background: #000` (`player.html:28-29`), so at the midpoint the picture is ½·new + ¼·old = 75% brightness when the two frames match. On a white-cyclorama film that is a visible blink.
- **2c/2d, mechanism confirmed.** `prefetch` loads into whichever element is not `.active` (`player.html:224-230`). With two clips queued, the second overwrites the first. With a clip queued inside the crossfade, the fading element is emptied and reloaded at opacity 0.85 (browser log, `xfade-browser.json`). Under JIT, a beat's first clip is ready 5.9–6.1 s before air, i.e. within ±0.25 s of the previous `playing`. That is why 15 to 18 of 62 seams in session A fall in the window (three were queued within 40 ms of `playing`, where file time cannot tell 2c from 2d). The 25 fps page recording could not separate 2d's visual cost from 2b's dip; the mechanism is confirmed, its magnitude is not. 2d is off the critical path; 2c is on it, and through the tunnel it costs 0.21–0.36 s.
- **1c is the only open starvation path that fired in session A**, and it fired mildly. The render path has 4.7 s of slack against 0.26 s of observed spread. The Director path has 4.7 s of slack against a 4.37 s maximum.

**Is true seamless playback feasible with discrete MP4s and element switching?** Near-seamless, yes: fix 2a–2d and 3c–3d and the seam becomes a one-frame timing uncertainty plus an ambience change. Truly seamless, no. Two elements cannot be joined frame-accurately, because `play()` starts 1–25 ms after the event [M], and their audio cannot be joined sample-accurately. The smallest change that can do it is **one media timeline**: each clip remuxed to a fragmented MP4 (`-movflags frag_keyframe+empty_moov+default_base_moof`, identical codec parameters every clip) and appended in order to a single MSE `SourceBuffer` in `sequence` mode. The browser's decoder then plays the clips as one stream. The `queued` event becomes "fetch and append"; the server keeps its hold as the story clock, and the browser seeks only on drift over a threshold. Audio can ride in the same buffer, with AAC priming trimmed by `appendWindowStart`. Alternatively it can be scheduled through Web Audio with equal-power crossfades, which also answers 3a and 3b as far as a player can. This is roughly a 100–150-line change to `player.html` plus one ffmpeg flag on the server. It depends on overlap trimming (2a); otherwise it joins duplicated frames perfectly.

---

## 4. Duration-policy comparison

Assumptions for the numbers: H100 box at 640×352, render wall R(L) ≈ 0.25 + 0.24 × latent frames (fitted to the 5, 6 and 8 s measurements), current JIT constants, Director answer p95 ≈ 4 s.

| Criterion | 1. One fixed duration (6 s) | 2. Small warmed menu (e.g. 4 / 6 / 8 s) | 3. Fully dynamic | 4. Model-predicted, snapped to a safe set |
|---|---|---|---|---|
| Render speed | 0.79–0.82× measured | same ratio per shape (R ∝ L); 4 s is slightly worse (fixed costs ~0.25 s) | dynamic kernels: speed unmeasured; eager fallback loses compile's 1.22× (≈ 0.97×) | as 2, plus the duration head's pass (unmeasured, small) |
| Compile / warm-up | one shape, 114–122 s warm-up (09-16) | 90–170 s per shape under fp8, or two compiles if dynamo's automatic-dynamic serves the third shape (unverified) | one dynamic compile (`LTX2_COMPILE_DYNAMIC=1`, untested) | as 2 |
| Starvation risk | lowest: one R estimate, measured spread 0.26 s | needs per-shape estimates; one EMA across shapes under-estimates a long clip by up to 2 s | per-length estimates impossible to pre-measure | length unknown until the render returns: the scheduler cannot compute the next `air` time before firing |
| Viewer latency | 9.5–15.5 s (mean 12.5) | a 4 s steered clip lands ~1.8 s sooner; an 8 s clip in flight adds up to 2 s | as 2 | unpredictable |
| Waste on abort | none on the GPU (JIT); ~1.1 s of Director output per dropped clip | same; a long in-flight clip commits more pre-line story | same | same |
| Pacing and completeness | the Director must write ~6 s of action; the model "won't stretch a moment" [F] | the best fit if the Director or a rule chooses well | best fit, highest variance | the model sizes the clip to the prompt [F], so the Director loses control of timing |
| Visual drift, audio | drift grows within a clip (grey ground at the end of busy clips, 09-15); one audio restart per 6 s | long clips: more drift, fewer seams, room for a full narrated line | as 2 | as 2 |
| Operational complexity | low | medium: warm list, shape contract, per-shape estimates | high | high: head not loaded, scheduling blind |

**Signals that could justify a longer clip**, all observable before the render. None has been validated.
1. A voice-over line longer than the clip can hold at a documentary pace (~2.5 words/s). 02 measured lines averaging ~25 words, i.e. 8–10 s against 6 s of picture.
2. A held state: the clip's action says nothing changes, or its end state equals its opening state. Held states also drift least.
3. No pending line and the viewer not typing. The player can send a boolean "typing" beacon on the first keystroke. How much warning it gives is measurable (R9); how often viewers speak is not predictable from 7 lines across two sessions.

A signal for a shorter clip is the inverse of 3: a line pending or being typed. It only pays if the short shape renders measurably faster than 6 s and is warmed.

---

## 5. Recommended duration and beat policy

**Duration: policy 1, owned by the runtime.**
- One render shape per run, fixed at startup, warmed, and checked against the server's warmed shapes (§7, Q10). 6 s today.
- The **runtime** chooses. The Director is told the length and writes action to fill it (02 §1.5). The compiler has no duration. LTX's duration head stays off.
- The unit is **frames**, not seconds. Delivered frames = render frames − overlap frames (after trimming, §8). Everything else derives from delivered frames / fps.
- Revisit the length only through R4 (4 vs 6 s). The trade is quantified: at L = 4 s the mean latency drops by ~2.5 s but the overlap is 9% of the frames. With 9-frame trimming, the H100 lands near 0.90×, which fails the §9 gate. Short clips need either a faster card or a 1-frame window.
- Policy 2 (menu) becomes worth an experiment only after R6 shows a second shape costs neither a long compile nor per-shape speed. Its first use would be the constrained hybrid: the Director tags a clip `hold` / `normal`, and the runtime maps it to a warmed shape and may override it for latency when a line is pending.

**Beats.**
- Keep `max_clips_per_beat: 4` as the schema cap and phrase the guidance in seconds ("a beat is up to 24 s"), as 01 E3 proposed. The cap never bound in session A (28 of 33 beats had two clips).
- **Cap beats that answer a viewer line at two clips.** Each clip costs ~1.1 s of Director time [M], and the answer call is the one on the critical path (§1.5). Put the cap in the user prompt's gates so the system prompt stays cached.
- Prefer more clips per beat to longer clips. Committed story per call is `clips × L`. Steering is unaffected by beat length, because unrendered clips are dropped. Clips already rendering or queued always play: at the moment of a line, the clip on air plus one or two more (6–12 s) are committed whatever the beat length.
- LLM call frequency when the viewer is quiet is 60 / (6 × mean clips) per minute: 5.0 at two clips, 2.5 at four. Longer beats save cost, not latency; the idle-path Director is pre-scheduled and never on the critical path.
- The largest remaining Director lever is **streaming the first clip**: parse the streamed `BeatPlan` and emit clip 1 as soon as its object closes. That needs `voiceover` (and the intervention fields) ordered before `clips` in the schema, which strict mode allows. Expected: the answer path falls from ~3.5–4 s to ~1.8 s [H]. This is an experiment, not a rule (R12).

---

## 6. Configuration cleanup plan (`i2v_duration` / `clip_seconds`)

### 6.1 Every use today [C]

| Where | What | Value in the live config |
|---|---|---|
| `src/models/config.py:38` | `i2v_duration: int = 5` | 5 |
| `config.py:39-44` | `clip_seconds: int = 0`; the comment says 0 means "the sum of the Director's shot durations (3-15 s)". That mechanism no longer exists | 6 |
| `src/graph/director_engine.py:92-94` | `clip_seconds = config.clip_seconds or config.i2v_duration` | 6 |
| `director_engine.py:263, 299, 496` | written into `EventRecord.duration` (int) and `Shot.duration_seconds` | 6 |
| `src/agents/director.py:78` | the same derivation again, for `{clip_seconds}` in the system prompt | 6 |
| `src/models/responses.py:31` | `Shot.duration_seconds: int = 5, le=15` | 6 |
| `src/i2v/ltxv.py:88, 143-145, 181` | `self.duration = 5` default; `snap(seconds × fps)` → 145 frames | 145 f |
| `server/ltx2_server.py:599` | snaps again; `X-Duration-S` = frames / fps | 6.0417 s |
| `src/eval/runner.py:446-447` | hold = ffprobe container duration, fallback `duration or i2v_duration` | 6.042 s |
| `runner.py:461, 487` | `clip_hold` for the in-flight slot of `_needed_at` | 6.0 s |
| `runner.py:654` | serial path: probe, fallback `i2v_duration` (5 for a 6 s clip) | 6.042 s |
| `runner.py:842` | `Playable.duration` before the render, fallback 5.0 | 6.0 |
| `src/story/pacing.py:37-40` | silence clock: `last_clip_duration`, then `Shot.duration_seconds`, then `i2v_duration` | 6.0 / 6.042 |
| `src/story/world.py:131, 261, 268` | story clocks (scene length, stagnation) sum `EventRecord.duration` | 6 |
| `server/remote_setup.sh` / server docstring | `LTX2_WARMUP_FRAMES` "145,145,145" / default "121,145"; `_setup_compile` comment lists 121–193 as if lengths varied | 145 |

Three values of "the clip length" coexist: 6 (story, prompt, in-flight slot), 6.0417 (frames), 6.042 (hold). The warmed shape is a fourth copy, kept by hand in a shell script on another machine.

### 6.2 Target

1. **One owner: a frozen `ClipShape`** (Pydantic, e.g. `src/i2v/shape.py`), built once at startup from `config.clip_seconds`, the overlap policy and the server's `/health` (fps, size, warmed frame counts). Fields: `fps`, `render_frames`, `overlap_frames`, `delivered_frames`, `delivered_seconds`. Everything in §6.1 reads it.
2. **Delete `i2v_duration`.** For one release, map it to `clip_seconds` when only it is set, and fail when both are set and differ.
3. `clip_seconds: float = 6.0`, validated > 0. Drop the "0 = variable" meaning and its obsolete comment.
4. `EventRecord.duration` and `Shot.duration_seconds` become float `delivered_seconds`. Old `world.json` files still load (Pydantic coerces ints). The story clock then counts real seconds.
5. **Hold = `delivered_seconds` from the server's `X-Num-Frames`**, not ffprobe's container duration. Keep ffprobe for the replay backend and for TTS audio only.
6. `_needed_at` uses the in-flight clip's `delivered_seconds`.
7. The client posts its shape to the server at startup (`POST /warmup {"frames": [153]}`) or refuses to start when `/health` does not list it (§7, Q10). `remote_setup.sh` stops being the source of the frame count.
8. Tests: update `tests/test_engine.py::test_fixed_clip_length_sets_every_clip` and `tests/test_i2v.py` (frame counts). Add one test asserting every consumer reads the same `ClipShape`.

`COMPILE_DYNAMIC`, for the record: `LTX2_COMPILE_DYNAMIC=1` passes `dynamic=True` to `compile_repeated_blocks` (`server/ltx2_server.py:462-464`). Torch then traces the transformer blocks with a symbolic token dimension, so one graph serves any frame count. It controls compilation only. It has nothing to do with the duration head, which predicts a frame count from the prompt and is unregistered on this server (`:271-272`). The dynamic path is untested, and its speed against the static graphs is unknown (R6).

**GPU consequences of several frame counts** [C][M]:
- Under fp8, every shape compiles separately: 90–170 s each on an H100, 114 s for the first 145-frame clip on 09-16.
- Dynamo marks a dimension dynamic after it changes once, so the second shape compiles a dynamic graph that may serve later shapes (unverified).
- Audio token counts change with length and recompile too (the reason `cache_size_limit` is 64).
- VAE decode memory grows with frames: 40 GB untiled for 193 frames at 960×544, which forces tiling (2.0 s instead of 1.3 s). At 640×352 it is harmless.
- The B200 needs three warm-up renders per shape.
- Overlap trimming changes the shape (145 → 153).
- A shape that was never warmed and is requested mid-film stalls the picture for minutes.

---

## 7. Queue, JIT and playback recommendations

Ordered by value over effort. Each names its dependency.

- **Q1. Log what the scheduler lives by.** Write `logs/sessions/<id>/timing.jsonl` with one record per event. Clip events: `fire`, `render_done` (with `X-Latency-S`, `X-Encode-S`, `X-Timings`, `X-Num-Frames`), `enqueued`, `playing`, `hold_end`. Viewer events: line `received` (text hash, segment on air, offset into it), `landed` (segment). Director calls: start, end, clip count, retry flag. Browser beacons, posted to a new `/beacon` route: `swap` (time from `playing` to the element's `playing`), `loadstart_on_playing` (prefetch missed), `waiting`. No behaviour change. Everything else in §9 depends on it.
- **Q2. Player seam fixes** (no dependency):
  - (a) Fade only the incoming element, over an outgoing element held at opacity 1, then hide the outgoing one. Or cut hard once the overlap is trimmed. This removes the 24% dip.
  - (b) Never assign a source to the element that is fading or active. Keep a small pool of three elements, or a queue of fetched Blobs, so a second queued clip cannot evict the first.
  - (c) An equal-power audio crossfade that overlaps the outgoing clip's tail, instead of a fade-in from silence.
  - (d) Optionally, start the next element on the outgoing element's media time (`requestVideoFrameCallback`) rather than on SSE arrival.
- **Q3. Pad audio to the video length on the server** (`apad` before `-shortest`, or trim the video to the audio). This removes the 32 ms hole at every clip end.
- **Q4. Deadline-aware line admission.** A line arriving at *t* targets the earliest clip *k* with `t + D_q ≤ fire(k) + τ`. `D_q` is the rolling p95 of Director answer calls; `τ` is the margin you accept spending (1.0 s). Otherwise clip *k* fires as planned and the line's beat starts at *k+1*. Keep the cancel-and-restart path for lines arriving during a call. This bounds the Director's intrusion into the buffer. It does not by itself lower latency (see Q7).
- **Q5. Quantiles instead of `1.25 × EMA`.** Use the rolling p95 of the last 20 render walls, keyed by shape, with the benchmark value as a floor, and likewise for the Director. An EMA with weight 0.5 swings with every sample; 1.25× of a stable 4.8 s spends 1.2 s on variance that is 0.05 s.
- **Q6. Make the buffer explicit.** Rename `lead_clips` to `startup_clips`, or document it as startup-only, and introduce `buffer_seconds` as the steady-state target (ready ≥ this long before air). Fix the N+3 statements in `CLAUDE.md` and the audit.
- **Q7. Lower `render_slack_seconds` only together with Q4.** Today's 3.0 s mostly pays for Director intrusion. With Q4 in place, 0.5–1.0 s suffices for the render path, whose spread is 0.26 s. Latency does not improve (the Director time moves from the slack into the admission rule: mean ≈ 12.4–12.9 s either way). The levers that do shorten it: answer beats ≤ 2 clips (−0.3 to −0.5 s of `D_q`), first-clip streaming (≈ −2 s [H]), a shorter clip (≈ −1.4 s per second of clip [H]), a faster card.
- **Q8. Drain the previous render before emitting the next clip.** Today `emit(k+1)` compiles clip *k+1* before render *k* is known to have succeeded (`runner.py:571` precedes `595`). Render *k* finishes about 1.2 s before `fire(k+1)` in steady state, so the reorder costs nothing. It makes Q9 possible.
- **Q9. Fail-soft render policy.**
  - (1) Retry once if `now + R_q + guard ≤ air(k)`.
  - (2) Otherwise restore the world from a snapshot taken before `apply_clip(k)` (the model is ~13 KB) and cut the beat with a note that clip *k* never rendered and the picture ends on clip *k−1*'s end state. The viewer sees a held frame for at most one clip, and the buffer refills.
  - An exception in `emit` should take the same route instead of ending the film (01 Q4).
- **Q10. Shape contract.** `/health` reports `warm_frames`. The client refuses to start, or warms first, when its `render_frames` is not listed. A mid-film shape change is a startup error, never a stall.
- **Q11. Then MSE** (§3), after Q2 and overlap trimming, if Q2 does not reach the §9 seam gate.

Not recommended: a deeper queue, because under JIT it is refilled only at startup and costs a clip of latency per clip of depth. Cancelling an in-flight render to admit a line one clip earlier: only lines in the first ~1.7 s of a render qualify, which needs a step-callback abort in the pipeline, for about a quarter of lines. Revisit after Q4 and first-clip streaming.

---

## 8. Continuity, restart and reference-image recommendations

### 8.1 Conditioning frames: 9 / 17 / 25 [C][M]

The window overwrites the first `(n−1)/8+1` latent frames at index 0 (02 §2.4). The token count is therefore unchanged for a fixed `num_frames`; only the delivered new content shrinks. With trimming and a fixed 144 delivered frames, the render grows instead:

| `continuity_frames` | Latent frames fixed | Duplicate if untrimmed | Render frames for 6.000 s delivered | Est. render wall | Ratio |
|---|---|---|---|---|---|
| 1 | 1 | 42 ms | 145 (today's shape) | 4.80 s | 0.80 |
| 9 (today) | 2 | 375 ms | 153 | ~5.04 s | ~0.84 |
| 17 | 3 | 708 ms | 161 | ~5.28 s | ~0.88 |
| 25 | 4 | 1042 ms | 169 | ~5.52 s | ~0.92 |

- 25 is ruled out on this card: it breaks the p95 gate.
- 17 costs 0.24 s per clip for more motion context and is worth one test (R5).
- **1 frame is the interesting arm.** It gives exact 6.000 s delivery on the current, already-warmed shape at no extra cost, but carries no velocity across the seam. Most end states are still poses, so it may be enough; R5 decides.
- 9 with trimming sits at ~0.84×, right under the gate. R3 must measure it.

**Trimming and sync.** Trim the first `overlap_frames` of the *new* clip and the same duration of its audio (0.375 s for 9 frames). The old clip then plays to its last frame and the new one continues from its frame *n*, which is the next frame in the motion [C]. The first clip of a run is seeded from one reference frame; trim that one frame or none. The delivered length becomes exactly `clip_seconds`, the hold matches the media, and the story clock stops drifting by 0.7%.

### 8.2 Dynamic continuity strength after steering

Keep 1.0, including after a line. Session A's lines landed inside the steered clip at 1.0 (breakdance, push-ups, monster; 02 §3.2). Below 1.0 the overlap tokens are regenerated rather than copied (02 §2.4). The seam then stops being an exact continuation, trimming becomes unsafe, and identity risk rises. If some requests prove too sluggish, lower the window, not the strength: condition the first steered clip on 1 frame instead of 9 at strength 1.0. With a fixed render shape that clip simply delivers 8 more frames (152 instead of 144), which the frame-based hold absorbs.

### 8.3 Server-restart recovery

Today a 400 containing "reference_image" makes the client re-send the story reference (`ltxv.py:196-200`). The chain restarts from the reference pose, which erased the red cap on 09-15 and put a second figure on screen on 09-17. The world model is never told the picture was reset.

Design:
1. **Detect explicitly.** The server generates a random `session_epoch` at startup and returns it in every response header and in `/health`. The client stores it; a change, a 400 or a connection error all mean "window lost".
2. **Restore from the last delivered clip.** The client already has it on disk. Extract its last `continuity_frames` with ffmpeg and send them as a new `window_frames` field (base64 PNGs, or the MP4 tail decoded server-side). The server installs them as `session["window"]`, plus the reference for `ref_image`. The H.264 round trip adds CRF-20 loss to frames the pipeline re-encodes anyway [H]; R7 measures the identity cost.
3. **Also persist the window on the server** (`np.save` after each turn, ~6 MB at 9 × 640×352). A process restart on the same box then restores itself without the client.
4. **Prompt.** The recovery clip uses the normal continuing prompt from the world model, never the opening prompt with `reference_pose`.
5. **Fallback.** Only if no delivered clip exists (turn 1) re-seed from the reference. The engine then tells the Director "the picture restarted from the reference image" through `beat_status`, and the transcript records a re-seed event.

### 8.4 Reference image

- **Normal operation: a story reference authored at the render aspect**, stored in the story folder. A 16:9 master of at least 1280×720 fits every size in use with at most 3.7% crop (640×352 is 1.818:1, 768×416 is 1.846:1, 960×544 is 1.765:1). The client resizes and centre-crops it to the server's exact size, so what it sends is exactly what conditions the model.
- **Letterboxing stays as the fallback.** The evidence supports it for the current square asset: the 380×381 Lego reference lost its head to a crop on 09-08 and the model invented one; white padding on a faint grey gradient rendered a framed inset; border-colour padding was seamless. For the Lego story the letterboxed frame *is* the intended composition, so the cleanest fix is to bake it into a 16:9 file once and validate it.
- **Validation at story load.** Static checks run in `Story.from_json`. Size checks run in a client preflight after `/health`, before the first Director call.
  - The file exists and decodes; RGB, or RGBA composited on the border colour.
  - The aspect ratio is within 5% of the render aspect → cover-resize, log the crop.
  - Aspect outside 5% and `reference_fit: pad` → letterbox with auto fill, plus a warning in the transcript and the run log.
  - Aspect outside 5% and `reference_fit: strict` → refuse to start.
  - The image is at least the render size; otherwise warn about upscaling (today's 381 px square is scaled ×1.14 to fill 80% of a 544 px frame).
  - For auto fill, the variance of the outer band is below a threshold. Otherwise padding will draw a visible frame (the 09-08 inset), so refuse to pad and require a correctly sized image.
  - `reference_pose` is non-empty. Its text cannot be checked against the pixels, so the loader only warns that it must describe the delivered frame, including margins.

---

## 9. Benchmark specification and pass/fail thresholds

### 9.1 What a run records

`benchmarks/runtime/<date>-<label>.json`, written by an extended `scripts/latency_probe.py` (the render-only benchmark) and by a `scripts/bench_report.py` that reads a film run's `timing.jsonl` (the end-to-end benchmark):

- **Identity of the run.** Git revision plus a dirty flag (`vast_run.sh run` ships `git stash create`, so record that hash); a config snapshot; the story id.
- **Hardware and stack.** GPU name, driver, box id and region; torch, diffusers and torchao versions; model id and variant.
- **Recipe.** dtype and quantisation, compile mode and dynamic flag, attention backend, encoder and preset.
- **Shape and continuity.** Width, height, fps, render/overlap/delivered frames, continuity frames and strength, anchor, seed policy.
- **Cold versus warm.** Server load time, warm-up time per shape, first-clip compile time; warm clips counted only from the fourth after warm-up.
- **Per-clip stages.** Text, conditions, transformer forwards, VAE decode, audio, encode (server `X-Timings`); HTTP transfer; client write; enqueue; queue wait; browser swap; mux (TTS mode).
- **Distributions.** Mean, median, p90, p95, max and sd of the render wall and of its real-time ratio (render wall / delivered seconds). The slope of the render wall over clip index.
- **Film-level outcomes.** Stall count and total held seconds; steering latency per line (received → steered clip on air); Director latency per call with clip count and retry flag; seam metrics from the browser beacons.

### 9.2 Where it lives

- `benchmarks/runtime/baseline.json`: the canonical record. Machine-readable, committed.
- A "Runtime baseline" table in `README.md` generated from it.
- One line in `CLAUDE.md` pointing to the file instead of restating numbers. The notes currently carry four different "×RT" figures.
- Superseded baselines stay in `benchmarks/runtime/` with their date. `docs/reviews/` keeps the narrative.

### 9.3 Gates

For the canonical setting at L = 6 s, over ≥ 60 warm clips with scripted lines (at least one line per 30 s, a third of them timed to arrive within 1 s before a fire):

| Metric | Pass | Today (session A) | Why this threshold |
|---|---|---|---|
| Render ratio p50 | ≤ 0.82 | 0.795 | leaves the p95/max budget |
| Render ratio **p95** | **≤ 0.85** | 0.802 | 0.9 s of headroom per clip, 3.5× the observed spread |
| Render ratio max | ≤ 0.92 | 0.820 | R < L − 0.5 s, so a slow clip never blocks the next fire |
| Render wall slope | ≤ 1 ms/clip over ≥ 150 clips | +2.2 ms/clip over 62 | a 30-minute film must not drift into the budget |
| Stalls (consumer wait > 50 ms) | 0 | 0 | the product promise |
| Minimum queue margin | ≥ 1.0 s | 1.87 s | covers a browser reload through the tunnel (0.36 s) with room |
| Steering latency p50 / p95 | ≤ 12.5 s / ≤ 16 s | ~12 s / (n = 4) | regression guard on the current policy |
| First picture after start | ≤ 25 s | 17.9 s | viewer patience, bootstrap included |
| Mid-film compile | none | none | a shape that was not warmed is a failure |
| Duplicate frames delivered per seam | 0 (after trimming) | 9 | — |
| Crossfade luminance dip | ≤ 5% | 24% | after Q2 |
| Browser swap p95 (prefetched) | ≤ 50 ms | 1–25 ms (09-17) | — |
| Swaps that reloaded on `playing` in steady state | 0 | ≤ 2/62 + startup | after Q2(b) |
| Hold minus media duration | ≤ 5 ms | 0.3 ms | — |

**On 0.85×.** As a mean it is too loose: a mean of 0.85 with a heavier tail would breach R < L − guard on some clips. As a p95, with a max gate at 0.92, it is justified by the budget above. The current setting passes with 0.05 to spare at p95. Two planned changes consume most of that: 9-frame trimming (+0.04) and the upward trend. A 4 s clip with trimming would fail on this card. So "0.85×" is adopted as a **p95** threshold with a max companion, not as a mean.

### 9.4 How coding agents detect regressions

- **Always, offline.** `python -m tests.run`, extended with a JIT simulation test. Drive `run_live` with the replay backend and a render delay drawn from the baseline's distribution; inject lines at adversarial times; assert zero stalls, a minimum margin, and latency bounds for the configured policy. It runs in seconds with the mock LLM and catches scheduling regressions in `runner.py`.
- **After touching `player.html` or `src/web/`.** The headless-Chromium seam check (`logs/_scratch/review-03/dim_*.py`, promoted to `scripts/seam_check.py`): luminance dip, reloads on `playing`, swap latency.
- **After touching `server/`, `src/i2v/`, the shape, or the recipe, and only with GPU time authorised.** `scripts/latency_probe.py --warm 30` against the baseline: compare p50, p95 and max with a tolerance of +3%. Any mid-run compile fails the run.
- **A film run with Q1 logging.** `scripts/bench_report.py logs/sessions/<id>` prints the §9.3 table and exits non-zero on a failed gate.

---

## 10. Implementation dependencies and risks

| Step | Depends on | Risk | Mitigation |
|---|---|---|---|
| 1. Q1 timing log + browser beacons | — | log volume (~10 records per clip) | JSON lines, one file per session |
| 2. `ClipShape`; delete `i2v_duration`; frame-exact hold | 1 (to verify) | int → float in `EventRecord`/`Shot` touches tests and transcripts | Pydantic coerces old ints; update two tests |
| 3. Player fixes Q2, audio pad Q3 | — | the three-element pool changes prefetch logic the tests do not cover | headless seam check (R2) |
| 4. Overlap trimming (server) + shape 153 + warm-up | 02 E1 / R3, 2, Q10 | +0.24 s render (→ ~0.84×); AAC priming at the trimmed start; one more shape to warm | measure first; if it fails the gate, try the 1-frame window (R5) |
| 5. Deadline-aware admission, quantiles, drain-before-emit, fail-soft | 1, 2 | lines arriving late move one clip later; scheduling bugs are timing-dependent | JIT simulation test (R1) before the box |
| 6. Restart recovery (epoch, `window_frames`, window on disk) | 2 | a new request field on server and client; frames extracted from H.264 | R7 on the box |
| 7. Reference validation + 16:9 Lego master | — | the Lego first clip changes if the master differs from the letterbox | render the first clip both ways once (R11) |
| 8. Answer beats ≤ 2 clips; first-clip streaming | — / schema field order | streaming changes the LLM backend; field order in strict schemas | behind a flag; R12 |
| 9. MSE player | 3, 4 | browser codec quirks (SPS changes, AAC priming) | same encoder settings every clip; R2 extended |
| 10. Duration menu / typing signal | 1, 2, 10-R6/R9 | compile time, per-shape estimates | only after experiments |

Cross-cutting risks: 02's compiler rewrite and the Director rule "write about `clip_seconds` of action" both read the shape, so step 2 should land first. The prompt series has no recorded owner for `remote_setup.sh` defaults, which step 4 must change in lockstep. The 01 review's double-line race interacts with Q4 and should be fixed in the same change.

---

## 11. Experiments required before rollout

GPU experiments need explicit authorisation. Estimates assume an H100 NVL at about $2.6/h.

| # | Question | Arms | Measures | Decides | Cost |
|---|---|---|---|---|---|
| R1 | Does deadline-aware admission remove Director-caused stalls without raising latency? | current vs Q4+Q5 at slack 3.0 / 1.0 / 0.5; Director tail with 5% retries at 2× | stall probability, min margin, latency distribution (Monte Carlo on session A's traces + the replay loop) | Q4, Q5, Q7 defaults | local |
| R2 | Do the player fixes remove the visible seam? | today vs Q2(a–c) vs MSE prototype | luminance dip, reloads on `playing`, swap latency, audio gap (headless Chromium, replay backend, tunnel emulated with `tc`-style delay) | Q2, whether MSE is needed | local |
| R3 | Trimming 9 frames: cost and seam | 145 untrimmed vs 153 trimmed, 30 warm clips | render wall p50/p95/max; motion step across seams (frame diff vs intra-clip); A/V sync | step 4 (= 02 E1) | ~30 min GPU |
| R4 | Clip length 4 vs 6 s | 97/105 vs 145/153 frames, 30 clips each, scripted lines | ratio, identity over a 20-clip chain, steering latency, seams per minute | §5 length | ~45 min GPU |
| R5 | Window 1 vs 9 vs 17 frames (trimmed) | three arms, same Director outputs replayed | motion continuity at seams, steering landing clip, render cost | §8.1 | ~45 min GPU |
| R6 | Cost of a second shape | static per shape vs dynamo automatic-dynamic vs `LTX2_COMPILE_DYNAMIC=1` | compile time per shape, forward time per shape vs static | whether a menu is ever affordable | ~30 min GPU |
| R7 | Restart recovery | kill the server at clip 10: re-seed from reference vs `window_frames` restore | identity and costume persistence across the restart, time to recover | §8.3 | ~20 min GPU |
| R8 | Long-run stability | 300 clips (30 min), gates of §9.3 | render slope, stalls, memory, Director tail | the baseline itself | ~40 min GPU + LLM |
| R9 | Typing lead time | player beacon on first keystroke, human sessions | first keystroke → submit (seconds) | whether typing is a usable signal | local |
| R10 | Duration head offline | predicted frames on session A's compiled prompts vs the Director's intent | agreement with held/moving clips | only if a menu is adopted | GPU, short |
| R11 | Reference master | letterboxed square vs 16:9 master of the same composition | first-clip identity and framing | §8.4 | ~10 min GPU |
| R12 | First-clip streaming | streamed parse vs whole-beat parse, 50 answer calls | time to clip 1, schema validity | §5, Q7 | LLM only |

---

## 12. Repository citations and discrepancies

### 12.1 Citations

- Runner: `src/eval/runner.py` — JIT constants 41-46; queue and lead 379, 391, 394; `_learn` 423-425; `_enqueue` 427-432; `_bg_render_and_enqueue` 434-455 (probe fallback 446-447); `clip_hold` 461; `_drain_prev` 471-478; `_needed_at` 480-488; `_plan` 490-514; `_listen` 516-554 (fire 524, Director lead 529, line cut 536-543); emit before drain 571 vs 595; pipelined path 585-608; serial path 609-741 (probe fallback 654); crash handling 748-750; consumer 758-801; `_playable` 836-853.
- Engine: `src/graph/director_engine.py` — `clip_seconds` 92-94; bootstrap 132-135; cap 150; `abort_beat` 182-212; `emit` 237-314 (duration 263, `EventRecord` 284-300).
- Director: `src/agents/director.py:78, 87-89`; `src/prompts/director.system.md:1, 48`.
- Client: `src/i2v/ltxv.py` — defaults 32-43; `duration` 88; `seconds_to_frames` 143-145; payload 177-191; re-seed 196-200; `snap_to_frame_grid` 233-235; `letterbox_reference` 255-280.
- Server: `server/ltx2_server.py` — warm-up env doc 85-88; `GenerateRequest` 157-171; offload/drop 266-273; compile 447-467; warm-up 482-495; `/generate` lock 523-534; `/health` 545-568; conditions 604-623; window 677-679; headers 717-732; encode (`-shortest`) 837-871; snap 874-876. `server/remote_setup.sh` (`LTX2_WARMUP_FRAMES`); `server/vast_run.sh` (`run`: `git stash create`).
- Config and models: `src/models/config.py:30-44, 104-109`; `configs/director.live.yaml:18-23, 48`; `src/models/responses.py:31`; `src/story/world.py:114-132, 261, 268`; `src/story/pacing.py:28-40`; `src/models/story.py:21-60`; `main.py:66-68`.
- Player: `src/playback/player.py:30-104`; `src/web/server.py:90-106, 143-151`; `src/web/player.html` — stage and crossfade CSS 28-30, `playVideo` 165-194, `fadeAudio` 196-205, `prefetch` 224-230, `tick`/buffering 233-246.
- Tests: `tests/test_live_timing.py`, `tests/test_engine.py:106-118`, `tests/test_i2v.py:47-93`, `tests/test_web.py`.
- Data: session logs as listed at the top; `logs/probe/*/probe.json`; `logs/vast/up-mvp.log`, `up-claude-0917.log`; `logs/_scratch/investigation-2026-09-17/{live,replay}-browser.jsonl`; this review's `logs/_scratch/review-03/`.
- Reviews: `docs/reviews/2026-09-16-real-time-plan.md`, `2026-09-17-continuity-and-buffering.md`, `2026-09-08-live-run.md` (reference cropping and padding), `01_architecture_feature_review.md` (§5.8, §7.4, Q4), `02_ltx_generation_prompting_review.md` (§2.4, §2.7, §3.3, §8, E1, E4).

### 12.2 Discrepancies

| # | Where | Says | Evidence | Conf. |
|---|---|---|---|---|
| 1 | `CLAUDE.md`; audit §5.29, §10.12, §11.4 | `lead_clips: 1` buffers one clip; lines land about N+3 (N+2 with 0) | JIT ignores `lead_clips`; the lead collapses to ~4.7 s after clip 4; N+2 or N+3 depends on arrival time, with either setting (§1.4-1.5) | High [M][C] |
| 2 | `2026-09-16-real-time-plan.md` ("Verified live") | "Playback … never waited" | Langfuse playback events for session V: holds of 0.14 s and 4.14 s after clips 5 and 6 | High [M] |
| 3 | `CLAUDE.md`, prompt 03 baseline | "0.76× real time" | server-side generate+encode (session V); the scheduler sees 0.79-0.82× on the box (session A); the B200 "0.76×" is one 768×416 5 s clip | High [M] |
| 4 | audit §9.9 | "a 6 s clip at 640×352 takes ~4.6 s total" | server-side; client wall 4.80 s mean | High [M] |
| 5 | `config.py:39-44` | `clip_seconds: 0` renders "the sum of the Director's shot durations (3-15 s)" | no shot durations exist; 0 falls back to `i2v_duration` (5 s) | High [C] |
| 6 | `configs/director.live.yaml:22` | `i2v_duration` is the "fallback seconds when no clip length is known" | it is also the ffprobe fallback in three places, so a failed probe holds a 6 s clip for 5 s (`runner.py:447, 654`; `pacing.py:40`) | High [C] |
| 7 | 2026-09-17 review §2; audit §12.5 | the player is "already seamless"; the crossfade means "no flicker" | the crossfade dims the picture 24% at every seam; the 09-17 replay log shows every clip reloaded on `playing` because a two-deep queue overwrote the prefetch | High [M] |
| 8 | audit §5.29 | "the viewer never sees a stall unless the GPU falls behind consistently" | the lead is not held after startup; the protection is `render_slack`, and a viewer line can spend it (1.87 s left at clip 35) | High [M] |
| 9 | `server/ltx2_server.py:85-88, 453-455` | warm-up default "121,145"; "clip lengths vary (121, 145, 169, 193)" | the live setup warms 145 three times; lengths no longer vary; the frame count lives in a shell default on another machine | High [C] |
| 10 | 01 review, header | Langfuse "usage details are not stored … for structured outputs" | all 35 Director generations of session A carry usage (output 206-669 tokens, cached input 2,944) | Medium [M] |
| 11 | prompt 03 | "the reported one-to-two-second seams" | the 1-2 s figure is session B's queue holds (per-clip engine); session A has none; what remains per seam is 0.1-0.4 s of player and overlap artefacts | High [M] |
| 12 | `2026-09-16-real-time-plan.md`, B200 table | "0.76×" for B200 fp8 768×416 | one warm clip; the two before it were 1.84× and 1.50× server-side with 3-6 s of untimed work | High [M] |
| 13 | audit §5.21, `ltxv.py` docstring | re-seeding "restores the chain" | it restarts the chain from the reference pose; costume and position reset (09-15 cap, 09-17 second figure); the world is not told | High [C][M] |
| 14 | runs in general | — | no run records hardware, revision, server stage timings, line receipt time or seam timing; session A's GPU and server timings are unrecoverable | High |

---

## Appendix: how the numbers were obtained

- **Consumer timeline**: `playback_start`/`playback_end` events in Langfuse. They are emitted by the consumer outside any turn span, so they sit outside the session's traces and were fetched by time window (`lf_events.py`). Hold = end − start; a gap = next start − end.
- **Render completion**: clip file mtimes (rsync preserved the box's clock). **Fire time**: the end of the turn span, since the span ends right after the render task is created (`runner.py:563-622`). Render wall = mtime − span end; queue margin = on-air − mtime (`margins.py`). Session B reproduces the 09-17 review's 45 s of holds over 27 transitions with this method.
- **Director and Showrunner**: Langfuse generations (start, end, usage), mapped to turns through their trace ids; beat sizes from `events.jsonl`.
- **Seam in the browser**: the real `WebPlayer` in the local Python, Playwright's headless Chromium in `logs/_scratch/pwenv` (the memory note's setup), ten replayed clips from session A. One run logged element events with computed opacity (`xfade_*`). One took screenshots of the picture's centre every 34 ms across three seams (`dim_*`). The 25 fps page recording under-samples the dip and was not used for magnitudes.
- **Clip streams**: `ffprobe` on session A clips 1, 2, 20, 35 and 48: video 145 frames, 6.041667 s; AAC 48 kHz, 6.010 s; container 6.042 s; 0.58-0.94 MB per clip.
