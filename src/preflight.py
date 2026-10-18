"""Setup checks: what a run needs, found before it starts and without a paid call.

`python main.py check` prints every finding and probes the LTX-2.5 server
when the config renders video. `play` runs the same checks without the
probe (the live loop checks the server itself before anything renders) and
stops on errors, so a missing key is a sentence, not a traceback.

Secrets are reported as set or missing, never shown.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass

from src.models.config import Config
from src.models.story import Story

OPENAI_KEYS_URL = "https://platform.openai.com/api-keys"


@dataclass
class Finding:
    level: str      # "ok", "warning" or "error"
    message: str


def check_setup(config: Config, story: Story, *, probe_server: bool = True) -> list[Finding]:
    found: list[Finding] = []

    def add(level: str, message: str) -> None:
        found.append(Finding(level, message))

    version = platform.python_version()
    add("ok" if sys.version_info >= (3, 11) else "warning",
        f"Python {version}" + ("" if sys.version_info >= (3, 11) else " (tested on 3.11 and newer)"))

    _check_llm(config, add)

    add("ok", f"story {story.title!r}")
    if not config.video_enabled:
        add("ok", "video off: the film plays as text, no GPU or ffmpeg needed")
        return found

    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        add("error", f"{' and '.join(missing)} not found on the PATH: install ffmpeg "
                     f"(apt install ffmpeg, brew install ffmpeg, or ffmpeg.org)")
    else:
        add("ok", "ffmpeg and ffprobe found")
    if not story.reference_image:
        add("error", "the story has no reference image (`picture.reference`); a video run starts from one")
    if probe_server:
        _check_server(config, story, add)
    return found


def _check_llm(config: Config, add) -> None:
    backend = config.llm_backend.lower()
    if backend == "mock":
        add("ok", "mock LLM: scripted beats, no API calls")
        return
    if backend != "openai":
        add("error", f"unknown llm_backend {config.llm_backend!r} (use \"openai\" or \"mock\")")
        return
    if not (config.director_model and config.showrunner_model):
        add("error", "director_model and showrunner_model must both be set in the config")
    if os.getenv("OPENAI_API_KEY", "").strip():
        add("ok", f"OPENAI_API_KEY is set (not tried against OpenAI); "
                  f"Director {config.director_model}, Showrunner {config.showrunner_model}")
    else:
        add("error", f"OPENAI_API_KEY is not set: create a key at {OPENAI_KEYS_URL} and put it in .env "
                     f"(cp .env.example .env)")


def _check_server(config: Config, story: Story, add) -> None:
    from src.i2v.ltxv import LtxvI2V, ReferenceRefused
    from src.i2v.shape import ClipShape, RendererNotReady

    with tempfile.TemporaryDirectory() as scratch:
        client = LtxvI2V(server_url=config.ltxv_server_url, output_dir=scratch)
        health = client._probe_health()
        shape = ClipShape.from_config(config, fps=client.frame_rate)
        try:
            client.check_shape(shape)
        except RendererNotReady as exc:
            add("error", str(exc))
            return
        gpu = (health or {}).get("gpu") or "unknown GPU"
        add("ok", f"LTX-2.5 server at {config.ltxv_server_url}: ready on {gpu}, "
                  f"{(health or {}).get('width')}x{(health or {}).get('height')}, warm [{shape.render_frames}] frames")
        try:
            verdict = client.check_reference(story.reference_image)
        except ReferenceRefused as exc:
            add("error", f"reference image refused: {exc}")
            return
        for note in verdict.get("warnings", []):
            add("warning", f"reference image: {note}")
