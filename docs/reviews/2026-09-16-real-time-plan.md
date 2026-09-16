# Getting LTX-2.5 to real time (2026-09-16)

A desk study, no GPU time. Goal: generate a clip in no more than its own playing time, so the player never waits, with quality secondary. The numbers below are our own measurements (2026-09-15, H100 PCIe) plus published benchmarks of the same stack; every projection is marked as such and is what the next GPU session must verify.

## Where the time goes

A 5 s clip (121 frames, 960×544, bf16, distilled 8 steps, everything resident) costs 12.4–13.3 s to generate plus 1.2 s to encode. The eight transformer forwards are 10.9 s of that (1.36 s each). The rest is text encoding, condition encoding, the VAE decode (0.6 s) and audio.

The forwards are compute-bound, not weight-bandwidth-bound: reading the 44 GB of bf16 weights once at the PCIe card's 1.76 TB/s takes 25 ms, and a forward takes 1.36 s. Token count at 960×544 × 121 frames is 30 × 17 × 16 = 8160 video tokens through 48 layers of width 4096. That is why fp8 matmuls, kernel fusion and faster tensor cores all pay, and why an H200's extra bandwidth does not. It is also why the SGLang docs' "fp8 gives headroom, not speed" is true for *their* fp8 (weight storage only, math in bf16) and not for torchao's dynamic fp8, which runs the matmul itself in fp8.

## Levers

| Lever | Expected gain on the forwards | Evidence |
|---|---|---|
| H100 SXM instead of PCIe | 1.3× | 989 vs 756 dense bf16 TFLOPS; Vast today prices the SXM at $2.27/h vs $2.47/h for the PCIe we have been renting |
| torch.compile, regional (`compile_repeated_blocks(fullgraph=True)`) | 1.15–1.3× | PyTorch's LTX-2 benchmark uses it; our ~30% MFU says there is fusion headroom |
| torchao float8 dynamic activation + weight (PerRow), selective | 1.3–1.5× on Hopper | diffusers-torchao: 1.27× CogVideoX-5b, 1.54× Flux on H100; unsloth ships the LTX-2.3 transformer in exactly this config, so the architecture quantizes |
| FlashAttention-3 backend (`set_attention_backend("_flash_3_hub")`) | 1.05–1.1× | attention is ~20% of the FLOPs at 8k tokens |
| Render size 960×544 → 768×416 | 1.6× (tokens 510 → 312 per frame) | linear in tokens, attention quadratic |
| Render size → 640×352 | 2.3× (220 tokens per frame) | as above; the model's own two-stage recipe generates at half size and upsamples, so low-res generation is native to it |
| B200 instead of H100 | 2–4× | PyTorch blog, LTX-2 768×512 × 121 frames, 40 steps, bf16 compiled: 16.2 s on a B200, i.e. ≤0.4 s per forward vs our 1.0 s equivalent; NVFP4 on top: 1.56× |
| NVFP4 (Blackwell only) | 1.5× | same benchmark; LPIPS noticeably higher than MXFP8, verify identity |
| Temporal 2× upsampling (generate at 12 fps) | up to 1.8× | `LTX2DFRTemporalRefinePipeline` exists but needs the DFR structure and a converted temporal upsampler; later |
| Encode: `-preset ultrafast` or overlap with the next clip's forwards | 1.2–1.8 s per clip back | server change: the lock covers the encode today |

Fixed cost outside the forwards is 2.5–3.5 s per clip (text, conditions, decode, audio, encode). At real-time targets that is 40% of a 5 s clip, so it matters as much as the forwards once they are fast.

## Hardware (Vast.ai, cheapest verified single-GPU offer, 2026-09-16)

| GPU | $/h | VRAM | Measured bandwidth | Dense bf16 | Verdict |
|---|---|---|---|---|---|
| H100 PCIe | 2.47 | 80 GB | 1.76 TB/s | 756 TFLOPS | what we used; slowest Hopper |
| H100 SXM | 2.27 | 80 GB | 2.5–2.9 TB/s | 989 TFLOPS | cheaper and faster: default from now |
| H100 NVL | 2.78 | 94 GB | 3.4 TB/s | ~835 TFLOPS | fine; extra VRAM for longer clips |
| H200 | 4.74 | 141 GB | 4.0 TB/s | 989 TFLOPS | same compute as SXM; not for us |
| B200 | 5.63 | 180 GB | 6.1 TB/s | 2250 TFLOPS | the real-time card; fp8 4.5, fp4 9 PFLOPS |
| RTX PRO 6000 Max-Q | 1.00 | 96 GB | 1.4 TB/s | ~200 TFLOPS | 3–4× slower than H100 in bf16; no |
| RTX 5090 | 0.43 | 32 GB | 1.45 TB/s | ~210 TFLOPS | 22B + Gemma do not fit resident; 25 s per 4 s clip reported; no |

## Recommendation

1. **Software first, on an H100 SXM** (one session, about $5): compile, fp8 dynamic quant, FA3, a warm-up render at startup, and the encode moved out of the lock. Measure each flag alone against the same seed and prompt, then together. Projected, at 960×544: forwards 10.9 s → 5–6 s; whole 5 s clip ≈ 7–8 s (1.5× real time). At 768×416: forwards ≈ 3 s; whole clip ≈ 4–4.5 s (0.85× real time). At 640×352: ≈ 3–3.5 s (0.65×).
2. **Then a B200 session** (about $10) with the same code. Projected: 960×544 at or under real time; 768×416 at 0.3–0.5× real time, which is the margin a buffer needs to stay full while the Director and encode run.
3. **The MVP setting** is whichever of (768×416, H100 SXM) or (960×544, B200) the measurements put under 0.8× real time. Clip length stays 5–6 s: shorter clips do not amortise the fixed cost, longer ones push steering latency (a line lands two clips later).

Not worth doing: SGLang (no custom conditioning, so no sliding window), porting to ltx-pipelines for fp8-scaled-mm or nvfp4-prequant (fallback only; it means reimplementing the window), H200, consumer or workstation Blackwell, two-GPU sequence parallel (a B200 does the same with no code), cache-based step skipping (8 distilled steps already), fewer sigmas (untested quality cliff).

## Sources

- PyTorch, "Faster Diffusion on Blackwell: MXFP8 and NVFP4 with Diffusers and TorchAO" (2026-04): LTX-2 on B200, 16.2 s bf16 / 13.7 s MXFP8 / 10.4 s NVFP4 at 768×512 × 121 frames × 40 steps. https://pytorch.org/blog/faster-diffusion-on-blackwell-mxfp8-and-nvfp4-with-diffusers-and-torchao/
- diffusers-torchao benchmarks (H100): float8 dynamic 1.27× CogVideoX-5b, 1.54× Flux. https://github.com/sayakpaul/diffusers-torchao
- unsloth/LTX-2.3-FP8: `Float8DynamicActivationFloat8WeightConfig`, PerRow, on the LTX transformer. https://huggingface.co/unsloth/LTX-2.3-FP8
- diffusers attention backends (`_flash_3_hub`, `sage_hub`, `flash_4_hub`). https://huggingface.co/docs/diffusers/main/en/optimization/attention_backends
- diffusers LTX-2 pipeline docs: two-stage recipe, latent upsamplers, `LTX2DFRTemporalRefinePipeline`, `output_type="latent"` on the condition pipeline. https://huggingface.co/docs/diffusers/main/en/api/pipelines/ltx2
- SGLang LTX-2.5 cookbook: fp8 weight-only "headroom, not speed"; no custom conditioning. https://docs.sglang.io/cookbook/diffusion/LTX/LTX2.5
- Lightricks, quantization formats: fp8-cast vs fp8-scaled-mm (TensorRT-LLM, Hopper), NVFP4 checkpoint. https://ltx.io/blog/quantization-formats-explained
- Lightricks/LTX-2 README: FA3 on Hopper, FA4 on Blackwell, `--quantization` flags. https://github.com/Lightricks/LTX-2
- VentureBeat: 10 s clip in 6.8 s on 2× GB200, Lightricks' own stack. https://venturebeat.com/technology/ltx-2-5-can-generate-a-10-second-ai-video-from-an-image-in-just-6-8-seconds-on-nvidia-superchips-and-its-open-weights
- Our measurements: docs/reviews/2026-09-15-substrate-sweep.md, section 3.

## Measured: H100 NVL, 2026-09-16 evening

Box: H100 NVL 94 GB on Vast ($2.66/h, Bulgaria), image `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel`, diffusers 0.41.0.dev0, torchao 0.16.0, distilled recipe, 24 fps, libx264 ultrafast, client on this machine through the ssh tunnel. `scripts/latency_probe.py`, results under `logs/probe/<setting>/`. Steady state (the first clip of a new shape under compile is excluded). "Server ×RT" is (generate + encode) / clip seconds; "wall ×RT" adds the transfer through the tunnel.

| setting | size | quant | compile | clip | forwards | decode | generate | encode | wall | server ×RT | wall ×RT |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 960×544 | bf16 | no | 5 s | 9.3 s | 2.0 s | 12.6 s | 1.1 s | 15.3 s | 2.75 | 3.06 |
| baseline | 960×544 | bf16 | no | 8 s | 15.5 s | 3.0 s | 20.0 s | 1.7 s | 23.6 s | 2.71 | 2.95 |
| + compile | 960×544 | bf16 | yes | 5 s | 7.6 s | 2.0 s | 10.9 s | 1.2 s | 13.6 s | 2.42 | 2.73 |
| + fp8 | 960×544 | fp8 | yes | 5 s | 6.0 s | 1.3 s | 8.2 s | 1.2 s | 12.2 s | 1.88 | 2.4 |
| + 768×416 | 768×416 | fp8 | yes | 5 s | 3.6 s | 0.8 s | 5.1 s | 0.8 s | 7.8 s | 1.17 | 1.56 |
| + 640×352 | 640×352 | fp8 | yes | 5 s | 2.3 s | 0.6 s | 3.5 s | 0.6 s | 5.7 s | 0.81 | 1.14 |
| + 640×352 | 640×352 | fp8 | yes | 8 s | 3.9 s | 0.9 s | 5.5 s | 0.8 s | 8.2 s | 0.80 | 1.02 |

What the levers gave on the forwards: compile 1.22×, fp8 1.27× (as published for CogVideoX), 768×416 1.67×, 640×352 another 1.55×. Together 4.0× on the forwards and 3.6× on the clip. Identity holds in every setting (`logs/probe/contact-kick.jpg`): fp8 changes nothing visible on the minifigure, the smaller frames are only softer.

Findings on the way:

- **The newer diffusers VAE decode wants 25 GB for 121 frames and 40 GB for 193 at 960×544**, untiled. Next to 70 GB of resident models that is an out-of-memory on a 94 GB card, which the 80 GB PCIe never hit on the older diffusers. Tiling (`LTX2_VAE_TILING=1`) makes it fit at 2.0 s instead of 1.3 s; fp8 frees enough for 5 s clips untiled but not 8 s. The server now turns tiling on by itself when a warm-up or a request runs out of memory. Untiled the decode is 0.6 s at 640×352.
- **FlashAttention-3 refuses attention masks and LTX-2 passes one**, also on a call my mask-aware routing did not catch. Parked: the fallback to PyTorch SDPA works, the expected gain was under 10%.
- **NVENC is unavailable in Vast containers** (the encode library is present, the container lacks the `video` driver capability). libx264 ultrafast costs 0.6–1.8 s per clip depending on size.
- **Under fp8, torch.compile compiles each clip shape separately**, 90–170 s each, and does not reuse the first shape for the second. In bf16 the compile was 20 s. A live run needs either every frame count warmed at startup (`LTX2_WARMUP_FRAMES`), `LTX2_COMPILE_DYNAMIC=1`, or a fixed clip length.
- **The tunnel costs 1.5–4.6 s per clip** (the MP4 travelling from Bulgaria). The client belongs on the GPU box; then wall ≈ server.
- torchao 0.17+ needs torch 2.10 and breaks diffusers' import on the 2.9 image; the setup pins `torchao<0.17`. Its C++ extensions do not load on 2.9.1 either, which does not matter for fp8 (`torch._scaled_mm`).

## Measured: B200, 2026-09-16 evening

Box: B200 180 GB on Vast ($5.68/h, Oregon, 8.8 Gbps: the 163 GB of weights arrived in under two minutes), same image and stack as the NVL run.

**One transformer-sized matmul (8160 × 4096 × 16384, the FF of a 5 s clip at 960×544), torch 2.9.1 + torchao 0.16:**

| format | time | throughput |
|---|---|---|
| bf16 (cuBLAS) | 0.67 ms | 1629 TFLOPS (72% of the 2250 peak) |
| fp8, torchao float8 dynamic per row | 0.74 ms | 1486 TFLOPS: slower than bf16 |
| MXFP8, torchao prototype | 0.95 ms | 1156 TFLOPS |
| NVFP4, torchao prototype, Triton | 0.57 ms | 1910 TFLOPS: 1.18× over bf16 |

On Hopper fp8 gave 1.27× on the forwards; on Blackwell with this torchao the fp8 path does not beat cuBLAS bf16, and only NVFP4 gains, at a quality cost that would need checking on the minifigure. The model reaches about 675 TFLOPS in its forwards against 1629 in the bare matmul, so on a B200 the non-matmul work (attention through PyTorch SDPA, norms, RoPE, the audio stream) is the larger share.

**Clips on the B200** (steady state; the first one or two clips after the warm-up carried 3–6 s of untimed work before settling, which the H100 did not show, so a B200 warm-up should render three clips of the production shape):

| setting | size | quant | compile | clip | forwards | decode | generate | encode | server ×RT | wall ×RT |
|---|---|---|---|---|---|---|---|---|---|---|
| fp8 + compile | 960×544 | fp8 | yes | 5 s | 3.0 s | 0.85 s | 4.9 s | 1.1 s | 1.19 | 1.5 |
| bf16 + compile | 960×544 | bf16 | yes | 5 s | 3.2 s | 0.85 s | 10.4 s | 1.1 s | 2.3 | 4.0 |
| fp8 + compile | 768×416 | fp8 | yes | 5 s | 1.7 s | 0.57 s | 3.0 s | 0.8 s | 0.76 | 0.98 |

The bf16 row is an anomaly, not a measurement of the model: the forwards were 3.2 s but every clip carried a constant 6 s outside the timed stages that the fp8 process on the same box did not have. Not chased; fp8 is the B200 configuration either way. The B200's forwards are 2.0× the H100 NVL's at the same setting, at 2.1× the price.

## Conclusion

Real time is reached on both cards, by different frames:

| card | $/h | setting | 5 s clip, server side | notes |
|---|---|---|---|---|
| H100 NVL / SXM | 2.3–2.8 | fp8 + compile, 640×352 | 3.5 s + 0.6 s encode = **0.81×** | 8 s clips the same ratio; identity intact |
| H100 NVL / SXM | 2.3–2.8 | fp8 + compile, 768×416 | 5.1 s + 0.8 s = 1.17× | not quite |
| B200 | 5.7 | fp8 + compile, 768×416 | 3.0 s + 0.8 s = **0.76×** | the same ratio at 1.44× the pixels |
| B200 | 5.7 | fp8 + compile, 960×544 | 4.9 s + 1.1 s = 1.19× | full size still needs NVFP4 (1.18× on the matmul) or FA4 |

Recommendation for the MVP: **an H100 SXM or NVL at 640×352 with `LTX2_QUANT=fp8 LTX2_COMPILE=1`**, at about $2.50 an hour. The B200 buys 768×416 at the same ratio for 2.1× the price; take it when the look matters more than the bill. Neither reaches real time at 960×544 with this stack.

What it takes to make the ratio hold in a live run, in order:

1. **Run the client on the GPU box.** The MP4 through the tunnel cost 1.5–4.6 s per clip, more than the margin; on the box the wall equals the server time. The web player then reaches the viewer through the same tunnel on its own port.
2. **One clip length.** Under fp8 every frame count compiles for 90–170 s on first use. Fix the Director's clip length (6 s is the natural one: 145 frames, steering lands in 12 s) and warm up that shape, three times on a B200. `LTX2_COMPILE_DYNAMIC=1` is the alternative and is untested.
3. **Buffer one clip** (`lead_clips: 1`) so the Director call and a slow clip never reach the screen; at 0.8× the buffer refills.
4. Untested levers if more is needed: `LTX2_COMPILE_MODE=max-autotune-no-cudagraphs`, NVFP4 on Blackwell (torchao 0.16 ran it at 1.18× on the matmul), FlashAttention-4 on Blackwell (the mask problem will be the same), `-preset ultrafast` is already on and NVENC needs a container with the `video` capability.

## Verified live on the box, 2026-09-16 night

H100 NVL (Ireland, $2.64/h, 27 Gbps), the setup script's defaults (`fp8`, compile, 640×352, warm-up of three 145-frame clips: 122 s, of which the first clip's compile is 114 s), and the film run **on the box** with `server/vast_run.sh run … --segments 10`: `configs/director.live.yaml` with `clip_seconds: 6` and `lead_clips: 1`, the player tunnelled to localhost:8765, two lines posted through the tunnel by `scripts/steer.py`. Logs and clips pulled to `logs/live-box/`.

| | |
|---|---|
| Clips | 10 × 6.04 s, every one rendered in 3.97–4.08 s + 0.57–0.61 s encode: 0.76× real time, no transfer |
| Production rate | 10 clips (60 s of film) in 62 s including the Director calls (about 5 s each, overlapped) |
| Playback | started after the two-clip lead, 4 s after the first Director call; never waited |
| Steering | a line posted while segment 3 played landed at 6 (absorbed); one posted during 6 landed at 8 (absorbed) |
| Picture | one cut (the opening), nine continues; identity intact through all ten (`logs/live-box/contact.jpg`) |

So the three steps hold: with the client on the box the wall time is the server time, one clip length means one compile at startup and 6 s of pacing, and the one-clip buffer keeps the screen fed while the Director writes. A viewer's line lands two to three clips after it is typed, 12–18 s.
