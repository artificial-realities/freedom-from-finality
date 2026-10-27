# Setting up FFF

For the coding agent helping someone run Freedom from Finality. They cloned the repo and asked you to set it up. Here's how.

Keep it short and friendly with them. Plain words, no jargon. You run the commands. They do the sign-ups, the clicking and the keys.

**Two rules.**

1. **Hands off the keys.** They paste their keys into `.env` themselves. Never read, print or copy that file. The checks below tell you whether a key works without showing it.
2. **Money needs a yes.** Renting the GPU costs money, and so does a film. Tell them the price first (step 4) and wait for a clear yes. Every time.

## How it runs

FFF makes its video live, one 6-second clip at a time. That takes a big NVIDIA GPU with 80 GB of memory. Most people don't have one, so by default you rent one on [Vast.ai](https://vast.ai) for the session. One script does it all. It rents the box, starts the video server, runs the film there and brings the page to their browser.

Got your own big GPU? Skip to [Your own GPU](#your-own-gpu).

## 1. Install

They need a Linux or macOS terminal (on Windows, WSL), Python 3.11+, git, ssh and rsync. Then:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt vastai
cp .env.example .env
```

## 2. Get the keys

Three accounts. Give them each link and say what to click. Each key goes on its own line in `.env`.

| Key | Where | What to do |
|---|---|---|
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Create a key. Add a few dollars of credit under Billing. This pays for the writing. |
| `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | First open [the model page](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) and click **Agree and access repository**. Then create a **Read** token. This unlocks the video model. |
| `VAST_API_KEY` | [cloud.vast.ai/manage-keys](https://cloud.vast.ai/manage-keys/) | Sign up, add credit ($20 is plenty to start) and create an API key. This rents the GPU. |

Vast also needs their ssh key to let them into the box. No `~/.ssh/id_ed25519.pub`? Make one with `ssh-keygen -t ed25519`. Show them the public key (`cat ~/.ssh/id_ed25519.pub`, safe to show) and have them paste it under **SSH Keys** on the same Vast page.

## 3. Check

```bash
server/vast_run.sh check
```

It tries the keys, reads their Vast credit, looks for the ssh key and prints what renting costs right now. It rents nothing. Fix whatever it complains about and run it again.

## 4. What it costs

Tell them this before renting, with the numbers `check` just printed. Two bills: Vast for the GPU, OpenAI for the writing.

- **The box:** about $2.50-3 an hour. It bills from `up` to `down`, and startup (5-20 min) counts.
- **The download:** a fresh box pulls about 145 GB. Some hosts charge for that, up to about $5.50, once per box. Many don't.
- **OpenAI:** about $0.50 per hour of film. A bit more if they type a lot.

Say they watch for 30 minutes. That's about $2 for the box and $0.25 for OpenAI, plus the download if the host charges for it: $2.50 to $8 all in. An hour is about $4 to $9.50.

The expensive mistake is forgetting `down`. The box bills every hour it exists, even when nothing plays.

## 5. Watch

Once they say yes:

```bash
server/vast_run.sh up      # rent and start the server: 5-20 min, mostly the download
server/vast_run.sh run     # then open http://127.0.0.1:8765/ and press Begin
```

`up` takes a while. Let it finish. `run` stays open while they watch, so run it in the background or have them run it in their own terminal. Ending it (Ctrl+C) brings their story home.

On the page:

- **Begin** the first time, **Continue** after that. They can type suggestions any time.
- **Stop** ends the film and saves it. **Continue** picks it up again, no new rental needed.
- Another film later in the same sitting? Just `run` again. No new `up`, no new download.

## 6. Always stop the meter

```bash
server/vast_run.sh down     # brings their story home, then destroys the box
server/vast_run.sh status   # should say "no instance recorded"
```

Run `down` when they're done for the day. Their story lives in `story/` on their own computer, so nothing is lost. If anything fails halfway through `up`, the box may already be billing: run `down`.

## If something breaks

- **"no offers match"**: no H100 is free right now. Try again in a while.
- **Stuck at "waiting for boot", or ssh fails**: some networks block Vast's ports. Try a VPN or another network, then `down` and `up` again.
- **The server never gets ready**: the script prints where the logs are. Read them. `down` if it's stuck.
- **`down` says the story couldn't come home**: run `server/vast_run.sh pull` first, then `down`.

## Your own GPU

FFF runs on a rented GPU by default. Got a big one yourself? Then skip Vast.

What's enough: an NVIDIA GPU with **80 GB** of memory, Hopper or newer. **H100** and **H200** are tested. An A100 80 GB runs, but too slowly, so the film keeps pausing. Gaming cards (4090, 5090) don't have the memory. Other 80 GB+ cards might work. Nobody has tried.

There's no code to change. Start the video server on that machine and play from the same machine: follow [On your own GPU machine](server/README.md#on-your-own-gpu-machine). You need `OPENAI_API_KEY`, `HF_TOKEN` and ffmpeg, no Vast key. `python main.py check` says what's missing. Server on a different machine from the player? Point to it with `LTXV_SERVER_URL` in `.env` (or `ltxv_server_url` in [configs/video.yaml](configs/video.yaml)). Clips then cross the network, and the film can stutter.

Then the only bill is OpenAI, about $0.50 per hour of film.
