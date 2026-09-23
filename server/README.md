# LTX-2.5 inference server

Resident image-to-video server for the prototype's video path. It holds the pipeline in memory and owns the sliding window: it keeps the tail of each generated chunk and conditions the next one on it, so the client only ever sends a prompt. LTX-2.5 generates synchronized audio alongside the video, so clips arrive with an ambient/SFX track already on them.

## Setup

The weights are gated. One-time:

1. Open [Lightricks/LTX-2.5-Diffusers](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) and click **Agree and access repository**.
2. Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

```bash
# On the GPU box:
pip install -r server/requirements.txt
export HF_TOKEN=hf_...
python ltx2_server.py          # downloads weights on first run, serves on :8000

# Placement:
LTX2_OFFLOAD=gpu python ltx2_server.py     # default: everything resident (~70 GB; 80 GB card; the pack ships no prompt enhancer and none is used)
LTX2_OFFLOAD=text python ltx2_server.py    # DiT + decoders resident (~40 GB), text encoder offloaded (~2x slower per clip)
LTX2_OFFLOAD=cpu python ltx2_server.py     # every component offloaded between uses (~4x slower per clip)
LTX2_VAE_TILING=1 python ltx2_server.py    # lower peak VRAM on decode

# On the client machine — tunnel, then run:
ssh -N -L 8000:localhost:8000 -p <PORT> root@<HOST>
python main.py play --config configs/director.live.yaml --player web
```

## Renting a GPU on Vast.ai

`server/vast_run.sh` automates the whole loop: rent the cheapest matching 80 GB offer, install the dependencies and start the server (everything resident), open the tunnel, wait for `/health`, run the client, destroy the instance.

```bash
server/vast_run.sh up                                                        # ~5-20 min, prints "server ready"; H100 SXM/NVL, fp8 + compile + 640x352 by default
server/vast_run.sh run                                                       # the film on the box; open http://127.0.0.1:8765/  (Ctrl+C stops it)
server/vast_run.sh pull                                                      # the run's transcript, world model and clips → logs/live-box/
LTX2_QUANT=fp8 LTX2_COMPILE=1 LTX2_WIDTH=768 LTX2_HEIGHT=416 server/vast_run.sh restart   # any LTX2_* setting, same box
python scripts/latency_probe.py --label sxm-fp8-768                          # seconds per clip second for this setting
server/vast_run.sh run configs/director.live.yaml                            # the web player at http://127.0.0.1:8765/, clips into logs/live/
server/vast_run.sh down                                                      # always, or the meter keeps running
```

Needs `VAST_API_KEY` and `HF_TOKEN` in `.env`, the `vastai` CLI, credit on the account, and your ssh key registered on Vast.

## Notes

**Two transformers, two recipes.** `Lightricks/LTX-2.5-Diffusers` ships `transformer/` (the **distilled** DiT, what `from_pretrained` loads by default) and `transformer_full/` (the full / SFT DiT). They need different recipes, and the server picks one with `LTX2_VARIANT`:

| `LTX2_VARIANT` | Weights | Recipe | Cost per clip |
|---|---|---|---|
| `distilled` (default) | `transformer/` | `sigmas=DISTILLED_SIGMA_VALUES` (8 steps), `guidance_scale=1.0`, STG and modality guidance off, one forward per step | 8 forwards |
| `full` | `transformer_full/` | 30 steps (`LTX2_STEPS`), the pipeline's default SFT guidance (CFG 3.0, STG 1.0, modality 3.0, rescale 0.7), `use_dynamic_shifting=True, shift_terminal=0.1` | 90 forwards |

Until 2026-09-09 the server ran the distilled weights with the full recipe (15 linear steps, CFG 3, STG 1), which the model card says will "quietly cost quality"; every live run and sweep before that date used this mismatched configuration. The 2026-09-15 sweep confirmed that this mismatch was the within-clip identity drift: on the distilled recipe the figure holds through the action with no anchor (`docs/reviews/2026-09-15-substrate-sweep.md`). The "broken distilled LoRA" of 2026-09-07 was `ltx-2.5-22b-distilled-lora-450-bf16.safetensors` applied on top of the already-distilled `transformer/`; that LoRA is for distilling `transformer_full/` and is not needed here, so the `LTX2_DISTILLED` switch is gone. The default render size is now the model card's 960×544 (`LTX2_WIDTH`/`LTX2_HEIGHT`); the client fits the seed image to whatever the server reports.

**Speed flags** (`docs/reviews/2026-09-16-real-time-plan.md`). The forwards are compute-bound, so the levers are faster silicon, low-precision matmuls, fusion and fewer tokens: `LTX2_ATTN=fa3` (FlashAttention-3 from the Hub; `fa4` on Blackwell, `sage`), `LTX2_QUANT=fp8` (torchao float8 dynamic on the DiT's large Linear layers; `mxfp8` / `nvfp4` on Blackwell), `LTX2_COMPILE=1` (regional torch.compile of the transformer blocks), `LTX2_WIDTH` / `LTX2_HEIGHT` (768×416 and 640×352 are the MVP candidates), `LTX2_ENCODER` (`auto`: h264_nvenc when available, else libx264 `ultrafast`). The server renders throwaway clips at startup (`LTX2_WARMUP`, frame counts in `LTX2_WARMUP_FRAMES`) so compilation and the first-clip CPU warm-up never reach a viewer; `/health` says `warming` until then, and a backend or the compiler that fails the warm-up is dropped with a warning rather than killing the server. `scripts/latency_probe.py --label <setting>` measures a setting: generation, encode and wall time per clip against its length (`ratio` below 1.0 means the player never waits), with the server's stage timings from the `X-Timings` header, into `logs/probe/<label>/`.

**Latency.** The 8 distilled forwards take ~11 s for 121 frames at 960×544 on an H100 PCIe, and with everything resident (`LTX2_OFFLOAD=gpu`, default, 70 GB) that is nearly the whole clip: 12–13 s per 5 s clip plus 1.2 s to encode. `text` offloads the Gemma text encoder and costs 28 s per clip (the encoder's round trip); `cpu` offloads everything and costs 57 s. The server logs a per-stage breakdown for every turn (`timings:`) and encodes the MP4 by piping frames into ffmpeg (libx264 veryfast, audio muxed from a WAV); diffusers' PyAV path took 17–24 s per clip on the Vast boxes and is kept behind `LTX2_ENCODER=av`.

**Identity anchor.** `LTX2ConditionPipeline` accepts several `LTX2VideoCondition`s: one at latent index 0 overwrites the leading tokens (the reference on a cut, the previous chunk's tail on a continue), and any other index is a *guiding keyframe* appended as its own tokens. A request with `anchor_strength > 0` adds the reference image at `anchor_index` (default -1, the last latent frame), so the clip has to return to the sheet; diffusers' long-form recipe uses anchor keyframes at 0.95. The client sets it from `ltxv_anchor_strength` / `ltxv_anchor_index`.

The client fits the seed image to the server's render size before sending it: a master within 5 % of the render aspect is cover-resized; any other reference is letterboxed with a warning (`i2v_reference_fit: pad`, fill `auto` continues the image's own border colour), or refused at startup under `strict`; a reference at a different aspect ratio would otherwise be cropped and the model invents what is missing. Every request carries a seed derived from the run's seed key and the segment, so a rerun reproduces the noise.

A request with `reset_window: true` is a cut: the server conditions on the reference image instead of the previous chunk's tail, which bounds identity drift. The Director engine sends it for shots marked `transition: cut`.

`ltxv_continuity_strength` in the client config controls the continuity-vs-steering trade-off. At `1.0` continuity is maximal but new prompts barely land; lower values let steering through at the cost of visual continuity.

**Not wired yet.** Two-stage distilled generation (stage 1 at half size, ×2 latent upsample, a 3-sigma tail at full size; the model card's "better quality" path) and the Ingredients IC-LoRA (`Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients`, a reference sheet of characters and props read in-context; Lightricks say it works on 2.5 at LoRA strength ~1.3, via `reference_conditions=` in the in-context / modular pipelines).
