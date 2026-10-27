# Freedom from Finality

An infinite, continuously generated story that plays in your browser while you type suggestions into it.

A man drawn in black lines stands alone in an empty white world. Two LLM roles — **Now** and **Then** — begin writing his story, and the story never stops. You watch. When you type something, the story listens and takes it wherever it goes. Characters appear, places form, things happen: nothing is scripted, nothing is predetermined, and the story develops its own meaning and continuity over time.

```
┌─▶ the viewer types a suggestion (or doesn't)
│     → Now (LLM) writes the next beat: 1-4 clips of action, dialogue and world changes
│     → code checks it against the world model and compiles a video prompt per clip
│     → LTX-2.5 renders each 6-second clip with sound, continuing from the end of the last one
└──── the browser plays the clips back to back
      (meanwhile Then, a second LLM, condenses the story so far and keeps the story's memory)
```

- **A story with a life of its own.** Two LLM roles write the film over a typed world model (characters, props, locations, an event log) that only code changes. Now writes one beat per call; Then keeps the story's memory in the background, never holding up the picture. The story keeps moving when nobody types.
- **Suggestions, not commands.** What you type is taken up in the story's own way, maybe with a twist, a character who resists, or a consequence that comes later. It reaches the screen about two clips after you type it.
- **One continuous picture.** [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5-Diffusers) renders every clip just in time on a GPU you host, each one conditioned on the last frames of the one before, so the film never cuts back to a fresh start. All the sound, the characters' voices included, comes from the video model.

## Run it

You need Python 3.11+, an [OpenAI API key](https://platform.openai.com/api-keys), ffmpeg, and the LTX-2.5 server on an NVIDIA GPU with 80 GB (an H100 has cost about $3 an hour to rent) with a Hugging Face token for the gated weights. [server/README.md](server/README.md) has the details.

```bash
git clone https://github.com/artificial-realities/freedom-from-finality.git
cd freedom-from-finality
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add your keys to .env
```

With a [Vast.ai](https://vast.ai) key it is three commands:

```bash
server/vast_run.sh up             # rent an H100, start the server (5-20 min the first time)
server/vast_run.sh run            # your story on it: open http://127.0.0.1:8765/ and press Begin
server/vast_run.sh down           # bring your story home and stop paying
```

On a machine with its own GPU, start the server there and run `python main.py play`, then open http://127.0.0.1:8765/. `python main.py check` says what is missing, without paid calls. The player has no login and listens on `127.0.0.1` only: everything typed into it is billed to your OpenAI key.

## Your story

There is one story, and it never ends. The page opens on the picture it stopped on: press **Begin** the first time, and **Continue** after that, and it goes on from the last clip you saw, with its world, its characters, everything that has happened and what the story has learned about you. **Stop** ends the film and saves it; so does closing the page (after a minute) or Ctrl+C. The story lives in the `story/` folder on your computer (never in git): copy the folder to back it up or move it to another computer. When the film runs on a rented GPU, `server/vast_run.sh` copies it there before the session and brings it home every minute and when the session ends.

The film itself is not kept: each clip is deleted once it has played, and only the few frames the next clip continues from are saved. Nothing is ever replayed; the story only goes on.

**What leaves your computer, and what stays.** While a film plays, Now and Then are calls to OpenAI with your key: each sends the story as the roles need it (the world, the recent clips, the story's past, Then's notes) and what you typed. The video is rendered on the GPU server you run. Nothing else is sent anywhere, and there is no telemetry. `story/viewer.jsonl` keeps what you typed for good, since it is the story's memory of you; `logs/films/` keeps technical records of the 20 most recent films (transcripts, timing, the models' raw outputs), which also contain what you typed, and none of which the story needs.

## How it works

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the engine, and [docs/decisions.md](docs/decisions.md) why it is built this way, with the evidence. Every story begins from the first frame in [frame_zero.json](frame_zero.json) (Frame Zero), and what the story will never show is in its [content policy](src/prompts/policy.md). Open problems: a slow Now call can still hold the picture for a moment; the sliding window trades continuity against how strongly a suggestion lands; and long films drift visually.

`pip install -r requirements-dev.txt`, then `ruff check .`. There is no test suite, by choice. Issues and pull requests are welcome.

## The name

*Freedom from Finality* (FFF) comes from a sentence in Myron W. Krueger's book *Artificial Reality*: "The freedom from finality allows a piece to grow, allows the computer to learn." Here it means a story with no ending, which keeps growing and keeps what it learns about the person watching.

## License

The code is [MIT](LICENSE). An [Artificial Realities](https://github.com/artificial-realities) project by Simen Bihaug-Frøyland ([@sbfroy](https://github.com/sbfroy)).

The LTX-2.5 weights by [Lightricks](https://huggingface.co/Lightricks) are not part of this repository: they are downloaded from Hugging Face under the LTX-2 Community License Agreement, which has its own conditions.
