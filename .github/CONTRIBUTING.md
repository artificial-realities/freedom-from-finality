# Contributing to Untitled Project

Thanks for stopping by. This is an experimental project under active development, so the most useful contributions are bug fixes, setup and documentation improvements, new stories, and findings from running it on hardware we haven't tried. For anything bigger, open an issue first so we can agree on the approach. Replies may be slow.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest        # offline: no API key, no GPU, no network
ruff check .
```

To run the film, see the [README](../README.md#quickstart). [AGENTS.md](../AGENTS.md) has the repository map, the conventions, and the checklist we use for every change, whether written by a person or a coding agent.

## Guidelines

- **Keep pull requests focused**, and match the style of the code around your change.
- **Tests stay offline.** Fake the models the way the tests do (the mock LLM, the stand-in renderer and servers in `tests/`); the suite fails any test that reaches beyond this machine.
- **Generation costs money.** Testing a change for real uses your own OpenAI key and, for video, your own GPU time. In the PR, say what you ran for real (a text film, a video film, which GPU), so reviewers know what was tested.
- **Keep the design's split.** The LLM roles propose, code validates and applies; prompts, schemas, the compiler and the engine stay free of any one story; no code scores story quality. [docs/decisions.md](../docs/decisions.md) explains why.
- **Never commit secrets** (`.env`) or run output (`logs/`).
- **Update the docs** (README, `docs/`, `server/README.md`, `.env.example`, `AGENTS.md`) when behaviour, configuration or setup changes.
- **New stories** live in their own folder under `stories/` with a `story.json` and a reference image ([field guide](../stories/README.md)). Keep them suitable for a general audience, and only include images and names you have the right to publish.

By contributing, you agree that your contributions are licensed under the [MIT License](../LICENSE).
