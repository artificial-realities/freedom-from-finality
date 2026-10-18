# Untitled Project

An AI-generated film that keeps playing in your browser while you type suggestions into it.

```
┌─▶ the viewer types a suggestion (or doesn't)
│     → Director (LLM) writes the next beat: 1-4 clips of action, dialogue and world changes
│     → code checks it against the world model and compiles a video prompt per clip
│     → LTX-2.5 renders each 6-second clip with sound, continuing from the end of the last one
└──── the browser plays the clips back to back
      (meanwhile the Showrunner, a second LLM, condenses the story so far and plans the next scene)
```

- **A story with a life of its own.** Two LLM roles write the film over a typed world model (characters, props, story threads, an event log) that only code changes. The Director writes one beat per call; the Showrunner keeps the story's memory and plans the next scene in the background, never holding up the picture. The story keeps moving when nobody types.
- **Suggestions, not commands.** A typed line is taken up in the story's own way, maybe with a twist, a character who resists, or a consequence that comes later. It reaches the screen about two clips after you type it.
- **One continuous picture.** [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) renders every clip just in time on a GPU you host, each one conditioned on the last frames of the one before, so the film never cuts back to a fresh start. All the sound, the characters' voices included, comes from the video model.

The bundled story is deliberately small: a LEGO minifigure in a white void who talks, meets a spaceman when a bit first fails badly, and gets visitors from other LEGO themes. A story is a folder with a `story.json` and a reference image ([stories/README.md](stories/README.md)); nothing in the engine knows about LEGO.

## Performance

One live film of 64 clips on an H100 SXM, client and server on the same machine: LTX-2.5 distilled (8 steps, unguided), fp8, regional `torch.compile`, 512×288 at 24 fps, 6-second clips.

| | |
|---|---|
| Render per 6 s clip | 3.5 s mean, 3.7 s p95 |
| Stalls (the picture held for the next clip) | none |
| Viewer line to its clip on air | 9.6-13.4 s |
| First picture | 13.8 s |
| LLM cost | about $0.50 per hour of film |

## Run it

Without a GPU the same film plays as text in the browser. You need Python 3.11+ and an [OpenAI API key](https://platform.openai.com/api-keys):

```bash
git clone https://github.com/artificial-realities/untitled-project.git
cd untitled-project
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add your OpenAI API key to .env
python main.py play               # open http://127.0.0.1:8765/
```

Video needs the LTX-2.5 server on an NVIDIA GPU with 80 GB (an H100 has cost about $3 an hour to rent), plus ffmpeg and a Hugging Face token for the gated weights. [server/README.md](server/README.md) has the details; with a [Vast.ai](https://vast.ai) key it is three commands:

```bash
server/vast_run.sh up             # rent an H100, start the server (5-20 min the first time)
server/vast_run.sh run            # play the film on it; open http://127.0.0.1:8765/
server/vast_run.sh down           # stop paying
```

`python main.py check --config configs/video.yaml` says what is missing, without paid calls. Every run writes a transcript, the world model, a timing log and the film itself to `logs/`. The player has no login and listens on `127.0.0.1` only: every line typed into it is billed to your OpenAI key.

## How it works

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the engine, [docs/decisions.md](docs/decisions.md) why it is built this way, with the evidence, and [docs/vision.md](docs/vision.md) what it is reaching for. Open problems: a slow Director call can still hold the picture for a moment; the sliding window trades continuity against how strongly a suggestion lands; and long films drift (the white void slowly becomes a baseplate floor).

`pip install -r requirements-dev.txt`, then `python -m pytest` and `ruff check .`: the tests run offline, without a key or a GPU. Issues and pull requests are welcome.

## License

The code is [MIT](LICENSE). An [Artificial Realities](https://github.com/artificial-realities) project by Simen Bihaug-Frøyland ([@sbfroy](https://github.com/sbfroy)); *Untitled Project* is a working title.

The LTX-2.5 weights by [Lightricks](https://huggingface.co/Lightricks) are not part of this repository: they are downloaded from Hugging Face under the LTX-2 Community License Agreement, which has its own conditions. LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse this project.
