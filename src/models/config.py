"""Run configuration loaded from YAML.

Shared by the `solo` and `mas` configs. `audio_enabled` and
`video_enabled` default to False — benchmark runs stay cheap.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Config(BaseModel):
    name: str
    description: str = ""
    graph: str  # "mas_graph" | "solo_graph"
    llm_backend: str  # "gemma" | "openai"
    model: str
    temperature: float = 0.7
    max_tokens_per_agent: int = 1024
    context_window_history: int = 5
    narrative_memory_target_tokens: int = 800

    video_enabled: bool = False
    video_buffer_clips: int = 6
    prebuffer_clips: int = 3
    i2v_backend: str = "dashscope"
    i2v_model: str = "wan2.6-i2v-flash"
    i2v_resolution: str = "720P"
    i2v_duration: int = 5
    i2v_audio: bool = False
    i2v_seed_image: str = "data/legoman.png"

    audio_enabled: bool = False
    elevenlabs_voice_id: str = ""
    # Minimum continuous silence required between voiceovers in the live
    # loop. The producer enforces this by gating Attenborough until
    # `silence_seconds` reaches this floor. Only meaningful when
    # `audio_enabled` is true; ignored in benchmark/play loops.
    min_pause_seconds: float = 5.0

    extra: dict = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
