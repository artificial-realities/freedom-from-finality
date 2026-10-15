# LTX-2.5 inference server

Resident image-to-video server for the prototype's video path. It holds the pipeline in memory and owns the sliding window: it keeps the tail of each generated chunk and conditions the next one on it, so the client only ever sends a prompt. LTX-2.5 generates synchronized audio alongside the video, so clips arrive with their sound already on them.

## Setup

The weights are gated. One-time:

1. Open [Lightricks/LTX-2.5-Diffusers](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) and click **Agree and access repository**.
2. Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

```bash
# On the GPU box (an 80 GB card, torch 2.9.1 with CUDA 12.8, ffmpeg on the PATH):
pip install -r server/requirements.txt
export HF_TOKEN=hf_...
. server/server_defaults.sh && python server/ltx2_server.py   # downloads weights on first run, serves on :8000

# On the client machine: tunnel, then run.
ssh -N -L 8000:localhost:8000 -p <PORT> root@<HOST>
python main.py play --config configs/director.live.yaml --player web
```

The client belongs on the GPU box if it can be: an MP4 through an ssh tunnel costs 1.5-4.6 s per clip.

## Renting a GPU on Vast.ai

`server/vast_run.sh` automates the whole loop: rent the cheapest matching 80 GB offer, install the dependencies and start the server, open the tunnel, wait for `/health`, run the film on the box, destroy the instance.

```bash
server/vast_run.sh up                                  # ~5-20 min, prints "server ready"; H100 SXM/NVL with server_defaults.sh
server/vast_run.sh run                                 # the film on the box; open http://127.0.0.1:8765/  (Ctrl+C stops it)
server/vast_run.sh pull                                # the run's transcript, world model and clips → logs/live-box/
LTX2_WIDTH=640 LTX2_HEIGHT=352 server/vast_run.sh restart   # any LTX2_* setting, same box; unset ones keep server_defaults.sh
python scripts/latency_probe.py --label h100-fp8       # does this setting render in real time?
server/vast_run.sh down                                # always, or the meter keeps running
```

Needs `VAST_API_KEY` and `HF_TOKEN` in `.env`, the `vastai` CLI, credit on the account, and your ssh key registered on Vast. Never edit `vast_run.sh` while it runs: bash reads the file as it goes.

## Notes

**Two transformers, two recipes.** `Lightricks/LTX-2.5-Diffusers` ships `transformer/` (the **distilled** DiT, what `from_pretrained` loads by default) and `transformer_full/` (the full / SFT DiT). They need different recipes, and the server picks one with `LTX2_VARIANT`:

| `LTX2_VARIANT` | Weights | Recipe | Cost per clip |
|---|---|---|---|
| `distilled` (default) | `transformer/` | `sigmas=DISTILLED_SIGMA_VALUES` (8 steps), `guidance_scale=1.0`, STG and modality guidance off, one forward per step | 8 forwards |
| `full` | `transformer_full/` | 30 steps (`LTX2_STEPS`), the pipeline's default SFT guidance (CFG 3.0, STG 1.0, modality 3.0, rescale 0.7), `use_dynamic_shifting=True, shift_terminal=0.1` | 90 forwards |

Use the distilled recipe. The distilled weights on the full recipe "quietly cost quality", as the model card says: the figure drifted within every clip, and no prompt fixed it. The full model on its own recipe gives the same identity at 3.5× the time. `ltx-2.5-22b-distilled-lora-450-bf16.safetensors` is for distilling `transformer_full/`, not for `transformer/`.

**Settings.** Every setting is an `LTX2_*` variable, listed in the docstring of `ltx2_server.py`. `server_defaults.sh` holds the ones `vast_run.sh` starts with: the distilled recipe, everything resident (`LTX2_OFFLOAD=gpu`, about 70 GB), fp8 matmuls (`LTX2_QUANT=fp8`), regional `torch.compile` (`LTX2_COMPILE=1`), 512×288 (exactly 16:9, the size the reference images are made for), and frames converted on the GPU and piped to ffmpeg as YUV (`LTX2_GPU_FRAMES`, `LTX2_PIPE_FORMAT`, `LTX2_X264_THREADS`). Without it the server runs bf16, uncompiled, at 960×544. The forwards are compute-bound, so the levers are the GPU, low-precision matmuls, compilation and fewer tokens. `LTX2_ATTN` picks the attention backend (FlashAttention-3 refuses LTX-2's attention masks and falls back to SDPA) and `LTX2_ENCODER` the encoder (`auto`: h264_nvenc where the container has it, which Vast's do not, else libx264). Offloading is slow: `text` (the Gemma encoder offloaded) about 2× per clip, `cpu` about 4×.

**Warm-up.** Under fp8 every frame count compiles separately (90-170 s), so a film has one clip length and the server renders throwaway clips at startup (`LTX2_WARMUP`, the frame counts in `LTX2_WARMUP_FRAMES`, which `vast_run.sh` takes from the client: `python -m src.i2v.shape <config>`). `/health` says `warming` until then and lists what it warmed in `warm_frames`; the client refuses to start without its shape, so a compile never lands on a viewer's clip. A backend or the compiler that fails the warm-up is dropped with a warning rather than killing the server. Each turn logs a per-stage breakdown (`timings:`), also sent in the `X-Timings` header.

**Continuity.** Only a film's first clip is conditioned on the reference image (`reset_window: true`); every other clip on the tail of the one before, which the server keeps. After a server restart the client sends that tail (`window_frames`) so the picture continues instead of resetting to the reference. `ltxv_continuity_strength` in the client config trades continuity against steering: at `1.0` continuity is maximal but new prompts barely land; lower values let steering through at the cost of visual continuity.

**Reference image.** The client fits the reference image to the server's render size before sending it. The live config's `i2v_reference_fit: strict` takes only a reference at the render aspect to the pixel and resizes it whole; `pad` cover-resizes a reference within 5 % of the aspect and letterboxes any other with a warning (fill `auto` continues the image's own border colour). Every request carries a seed derived from the run's seed key and the segment, so a rerun reproduces the noise.

**Identity anchor.** `LTX2ConditionPipeline` accepts several `LTX2VideoCondition`s: one at latent index 0 overwrites the leading tokens (the reference or the previous chunk's tail), and any other index is a *guiding keyframe* appended as its own tokens. A request with `anchor_strength > 0` adds the reference image at `anchor_index` (default -1, the last latent frame). It is off in the configs: on the distilled recipe identity holds without it, an end anchor suppresses the clip's closing action and a mid anchor inserts an idle beat.

**Not wired yet.** Two-stage distilled generation (stage 1 at half size, ×2 latent upsample, a 3-sigma tail at full size; the model card's "better quality" path) and the Ingredients IC-LoRA (`Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients`, a reference sheet of characters and props read in context; Lightricks say it works on 2.5 at LoRA strength ~1.3, via `reference_conditions=` in the in-context / modular pipelines).
