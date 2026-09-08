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

# Lower VRAM usage:
LTX2_OFFLOAD=cpu python ltx2_server.py     # CPU offload between calls
LTX2_VAE_TILING=1 python ltx2_server.py    # lower peak VRAM on decode

# On the client machine — tunnel, then run:
ssh -N -L 8000:localhost:8000 -p <PORT> root@<HOST>
python main.py play --config configs/mas.live.yaml
```

## Renting a GPU on Vast.ai

`server/vast_run.sh` automates the whole loop: rent the cheapest matching 80 GB offer, install the dependencies and start the server with CPU offload, open the tunnel, wait for `/health`, run the client, destroy the instance.

```bash
server/vast_run.sh up                                                        # ~5-15 min, prints "server ready"
server/vast_run.sh run configs/director.live.yaml data/live_scenario.json    # renders into logs/live/
server/vast_run.sh down                                                      # always, or the meter keeps running
```

Needs `VAST_API_KEY` and `HF_TOKEN` in `.env`, the `vastai` CLI, credit on the account, and your ssh key registered on Vast.

## Notes

The server runs the **base model** at 15 sampling steps — the quality/speed sweet spot tested on A100-80GB (~170s/clip with CPU offload). The distilled LoRA (`LTX2_DISTILLED=1`) is currently broken in diffusers ≤0.41 (missing connector modules cause corrupted output); leave it off until a future diffusers release wires those up.

A request with `reset_window: true` is a cut: the server conditions on the reference image instead of the previous chunk's tail, which bounds identity drift. The Director engine sends it for shots marked `transition: cut`.

`ltxv_continuity_strength` in the client config controls the continuity-vs-steering trade-off. At `1.0` continuity is maximal but new prompts barely land; lower values let steering through at the cost of visual continuity.
