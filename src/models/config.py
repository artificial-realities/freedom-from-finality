"""Run configuration loaded from YAML.

`configs/text.yaml` plays the story as text; `configs/video.yaml` renders
it on the LTX-2.5 server. Anything a config leaves out takes the default
below.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "film"
    llm_backend: str = "openai"

    # ── The two roles ──────────────────────────────────────────────
    now_model: str = ""
    then_model: str = ""
    now_reasoning_effort: str = ""
    then_reasoning_effort: str = ""
    temperature: float = 0.7
    max_tokens_now: int = 2048
    max_tokens_then: int = 2048

    # ── Video ──────────────────────────────────────────────────────
    video_enabled: bool = False
    clip_seconds: float = 6.0
    startup_clips: int = 0
    buffer_seconds: float = 4.7
    ltxv_server_url: str = "http://localhost:8000"
    ltxv_continuity_strength: float = 1.0

    # ── The story's pace ───────────────────────────────────────────
    recent_clips: int = 6
    max_clips_per_beat: int = 4
    answer_max_clips: int = 2
    compaction_interval: int = 10
    then_min_gap: int = 4

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        data.setdefault("name", Path(path).stem)
        server_url = os.getenv("LTXV_SERVER_URL", "").strip()
        if server_url:
            data["ltxv_server_url"] = server_url
        return cls(**data)
