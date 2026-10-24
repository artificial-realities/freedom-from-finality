# LTX-2.5 inference server

The video half of the film. `ltx2_server.py` holds LTX-2.5 resident on the GPU and owns the sliding window: it keeps the tail of each clip and conditions the next one on it, so the client only ever sends a prompt and gets back an MP4 with its sound.

It runs one recipe, the one every number in the README was measured with: the distilled model on its own schedule (8 steps, unguided), everything resident on an 80 GB card (about 72 GB), fp8 matmuls, compiled transformer blocks, 512×288 at 24 fps, frames converted on the GPU and encoded with x264, and every clip's audio held under -18 LUFS. On a GPU without fp8 (before Hopper) it falls back to bf16, slower than real time.

## Requirements

Linux, an NVIDIA GPU with 80 GB (tested on H100 SXM and NVL, and H200 NVL), torch 2.9.1 with CUDA 12.8 (the `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel` image has it), ffmpeg, and about 72 GB of disk for the weights. The weights are gated; once:

1. Open [Lightricks/LTX-2.5-Diffusers](https://huggingface.co/Lightricks/LTX-2.5-Diffusers), read the LTX-2 Community License and click **Agree and access repository**.
2. Create a read token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) and put it in `.env` as `HF_TOKEN`.

## Renting a GPU on Vast.ai

`vast_run.sh` rents the cheapest matching H100, installs and starts the server, tunnels it to `localhost:8000`, and runs the film on the box, so no clip crosses the internet (an MP4 through the tunnel costs 1.5-4.6 s). Your adventure stays yours: `run` copies it to the box, brings it home every minute and when the film stops, and `down` brings it home before destroying the box.

```bash
server/vast_run.sh up        # rent and start; ready in 5-20 min (weights download, then the warm-up)
server/vast_run.sh run       # your adventure on the box; open http://127.0.0.1:8765/ (Ctrl+C stops it)
server/vast_run.sh pull      # bring your adventure home, and the run's transcript and timing log → logs/box/
server/vast_run.sh down      # always: renting bills by the hour until this
server/vast_run.sh status    # what is rented, and the server's /health
```

Needs `VAST_API_KEY`, `HF_TOKEN` and `OPENAI_API_KEY` in `.env`, the `vastai` CLI (`pip install vastai`), credit on the account, and your ssh key registered on Vast. Arguments after `run` reach `main.py` (`server/vast_run.sh run configs/video.yaml --segments 20`). Never edit `vast_run.sh` while it runs: bash reads the file as it goes.

## On your own GPU machine

```bash
pip install -r server/requirements.txt                 # torch 2.9.1 + CUDA 12.8 already installed
set -a; . ./.env; set +a                               # HF_TOKEN
LTX2_WARMUP_FRAMES=$(python -m src.i2v.shape configs/video.yaml) python server/ltx2_server.py   # 127.0.0.1:8000

# then, in a second shell on the same machine:
python main.py check --config configs/video.yaml       # reachable, ready, the clip shape warmed?
python main.py play --config configs/video.yaml
```

Open the player through a tunnel from your computer: `ssh -N -L 8765:localhost:8765 <user>@<gpu-host>`. The server listens on `127.0.0.1` (`LTX2_HOST`, `LTX2_PORT`) and has no authentication: reach it through ssh or from the same machine.

## Notes

**Warm-up.** Under fp8 every frame count compiles for 90-170 s, so a film has one clip length and the server renders throwaway clips at startup for it (`LTX2_WARMUP_FRAMES`, default the video config's 153 frames, three times). `/health` says `loading`, then `warming`, then `ready`, and lists `warm_frames`; the client refuses a server that has not warmed its shape, so a compile never lands on a viewer's clip.

**Continuity.** Only an adventure's first clip is conditioned on the reference image; every other clip on the last 9 frames of the one before. A client run's first request (`start`) begins a fresh session from either the reference (a new adventure) or the client's saved copy of those frames (`window_frames`: a resume, or after this server restarted), so nothing from an earlier session carries over and the picture continues instead of resetting. `ltxv_continuity_strength` in the client config trades continuity against steering: at 1.0 a new prompt barely lands.

**Why the distilled recipe.** The distilled weights sampled with the full model's recipe (15 steps, CFG 3, STG) made the figure drift within every clip, and no prompt fixed it. On their own recipe the figure holds; the full model on its recipe gives the same identity at 3.5× the time. See [docs/decisions.md](../docs/decisions.md).

**Dependencies.** `requirements.txt` pins diffusers to the 0.41.0 release. torchao 0.17+ needs torch 2.10, so bump the two together.
