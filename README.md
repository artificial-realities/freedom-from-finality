# Untitled Project

An infinite, continuously generated story that plays in your browser while you type suggestions into it.

A stick figure stands alone in an empty white world. Two LLM roles — **Now** and **Then** — begin writing its story, and the story never stops. You watch. When you type something, the story listens and takes it wherever it goes. Characters appear, places form, things happen: nothing is scripted, nothing is predetermined, and the story develops its own meaning and continuity over time.

```
┌─▶ the viewer types a suggestion (or doesn't)
│     → Now (LLM) writes the next beat: 1-4 clips of action, dialogue and world changes
│     → code checks it against the world model and compiles a video prompt per clip
│     → LTX-2.5 renders each 6-second clip with sound, continuing from the end of the last one
└──── the browser plays the clips back to back
      (meanwhile Then, a second LLM, condenses the story so far and keeps the story's memory)
```

- **A story with a life of its own.** Two LLM roles write the film over a typed world model (characters, props, locations, an event log) that only code changes. Now writes one beat per call; Then keeps the story's memory in the background, never holding up the picture. The story keeps moving when nobody types.
- **Suggestions, not commands.** A typed line is taken up in the story's own way, maybe with a twist, a character who resists, or a consequence that comes later. It reaches the screen about two clips after you type it.
- **One continuous picture.** [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) renders every clip just in time on a GPU you host, each one conditioned on the last frames of the one before, so the film never cuts back to a fresh start. All the sound, the characters' voices included, comes from the video model.

## Run it

You need Python 3.11+, an [OpenAI API key](https://platform.openai.com/api-keys), ffmpeg, and the LTX-2.5 server on an NVIDIA GPU with 80 GB (an H100 has cost about $3 an hour to rent) with a Hugging Face token for the gated weights. [server/README.md](server/README.md) has the details.

```bash
git clone https://github.com/artificial-realities/untitled-project.git
cd untitled-project
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add your keys to .env
```

With a [Vast.ai](https://vast.ai) key it is three commands:

```bash
server/vast_run.sh up             # rent an H100, start the server (5-20 min the first time)
server/vast_run.sh run            # play your adventure on it; open http://127.0.0.1:8765/
server/vast_run.sh down           # bring your adventure home and stop paying
```

On a machine with its own GPU, start the server there and run `python main.py play`. `python main.py check` says what is missing, without paid calls. The player has no login and listens on `127.0.0.1` only: every line typed into it is billed to your OpenAI key.

## Your adventure

There is one adventure, and it never ends. The first `play` begins it from the first frame; every `play` after that resumes it from the last moment you saw, with its world, its characters, everything that has happened and what the story has learned about you. It lives in the `adventure/` folder on your computer (never in git): copy the folder to back it up or move it to another computer. When the film runs on a rented GPU, `server/vast_run.sh` copies it there before the film and brings it home every minute and when the film stops.

The film itself is not kept: each clip is deleted once it has played, and only the few frames the next clip continues from are saved. Nothing is ever replayed; the story only goes on. `logs/` holds technical records of each run (transcripts, timing, the models' raw outputs), none of which the adventure needs.

## How it works

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the engine, and [docs/decisions.md](docs/decisions.md) why it is built this way, with the evidence. Every adventure begins from the first frame in [frame_zero.json](frame_zero.json), and what the story will never show is in its [content policy](src/prompts/policy.md). Open problems: a slow Now call can still hold the picture for a moment; the sliding window trades continuity against how strongly a suggestion lands; and long films drift visually.

`pip install -r requirements-dev.txt`, then `ruff check .`. There is no test suite, by choice. Issues and pull requests are welcome.

## License

The code is [MIT](LICENSE). An [Artificial Realities](https://github.com/artificial-realities) project by Simen Bihaug-Frøyland ([@sbfroy](https://github.com/sbfroy)); *Untitled Project* is a working title.

The LTX-2.5 weights by [Lightricks](https://huggingface.co/Lightricks) are not part of this repository: they are downloaded from Hugging Face under the LTX-2 Community License Agreement, which has its own conditions.
