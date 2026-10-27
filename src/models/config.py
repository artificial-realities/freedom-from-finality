"""Run configuration loaded from YAML.

`configs/text.yaml` plays the story as text; `configs/video.yaml` renders
it on the LTX-2.5 server. Anything a config leaves out takes the default
below; an unknown key is an error.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The config's name, for the films' folders in logs/films/ (default:
    # the file's name).
    name: str = "film"

    # ── The two roles ──────────────────────────────────────────────
    # The OpenAI models of Now (one call per beat, on the critical path)
    # and Then (memory, off it); both gpt-5.6-luna in the configs.
    now_model: str = ""
    then_model: str = ""
    # A reasoning model's effort ("none" keeps hidden reasoning off, so it
    # adds no latency); "" for a chat model, which takes no effort.
    now_reasoning_effort: str = ""
    then_reasoning_effort: str = ""
    # Each reply's cap: one cut off at its cap is lost whole, so the caps
    # sit well above the largest replies seen (about 1,300 tokens each).
    max_tokens_now: int = 4096
    max_tokens_then: int = 4096
    # How long a call may take. Now's median is 6.5 s and past about 10 s
    # the picture holds anyway; a failed Now call is made once more, then
    # the film stops. Then is off the critical path, and the OpenAI client
    # retries it `then_retries` times.
    now_timeout_s: float = 20.0
    then_timeout_s: float = 90.0
    then_retries: int = 2

    # ── Video ──────────────────────────────────────────────────────
    # Render the story on the LTX-2.5 server; off, it plays as text.
    video_enabled: bool = False
    # The one clip length: the run's clip shape (`src/i2v/shape.py`).
    clip_seconds: float = 6.0
    # Clips rendered before playback starts, beyond the first.
    startup_clips: int = 0
    # How far ahead of its time on air a clip's render is fired, beyond the
    # render's own estimate: what a slow Now call or render can eat.
    buffer_seconds: float = 4.7
    # The server's address (LTXV_SERVER_URL overrides it).
    ltxv_server_url: str = "http://localhost:8000"
    # How hard each clip holds the last frames of the one before: 1.0 is
    # the most continuity and the least steering.
    ltxv_continuity_strength: float = 1.0

    # ── The story's pace ───────────────────────────────────────────
    # The clips Now reads word for word.
    recent_clips: int = 6
    # The most clips a beat may have, and a beat that answers the viewer
    # (its latency is the steering delay).
    max_clips_per_beat: int = 4
    answer_max_clips: int = 2
    # Then runs every this many clips (and once before the first), at most
    # one at a time and at least `then_min_gap` clips apart.
    then_every: int = 10
    then_min_gap: int = 4

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        data.setdefault("name", Path(path).stem)
        server_url = os.getenv("LTXV_SERVER_URL", "").strip()
        if server_url:
            data["ltxv_server_url"] = server_url
        return cls(**data)
