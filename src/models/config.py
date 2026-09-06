"""Run configuration loaded from YAML.

`audio_enabled` and `video_enabled` default to False so headless dev
runs stay cheap.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Config(BaseModel):
    name: str
    description: str = ""
    graph: str  # "mas_graph"
    llm_backend: str  # "openai"
    model: str
    temperature: float = 0.7
    max_tokens_per_agent: int = 1024
    context_window_history: int = 5
    narrative_memory_target_tokens: int = 800

    # Episodic memory backend (plan 02):
    #   "prose" — Spock rewrites the rolling narrative_memory every turn (legacy)
    #   "mem0"  — embedded Mem0 store; agents retrieve relevant memories per
    #             turn and Spock stops rewriting prose memory
    memory_backend: str = "prose"

    # When True, dispatch to a continuous live loop instead of the
    # turn-by-turn `run_play`. Combined with `video_enabled`:
    #   live + video_enabled  → run_live      (clips + ffplay window)
    #   live + !video_enabled → run_live_text (Tk popup, no clips)
    # `play` (no scenario, !live) is the sequential terminal loop used
    # for headless dev work.
    live: bool = False

    video_enabled: bool = False
    # Producer lead, in clips. The live producer renders ahead of the
    # player by this many clips and the consumer waits for `lead_clips + 1`
    # clips to be queued before starting playback. 0 = no startup wait,
    # no enforced race-ahead (queue size 1, render and play overlap by one
    # clip naturally). Bump above 0 to re-enable the "delay-as-feature"
    # narrative smoothing once i2v latency drops enough that a sustained
    # lead is realistic.
    lead_clips: int = 0
    i2v_backend: str = "dashscope"
    i2v_model: str = "wan2.6-i2v-flash"
    i2v_resolution: str = "720P"
    i2v_duration: int = 5
    i2v_audio: bool = False
    i2v_seed_image: str = "data/legoman.png"

    # LTX-2.5 backend (when i2v_backend == "ltxv"):
    ltxv_server_url: str = ""
    # How hard each chunk holds the previous one. 1.0 = maximum visual
    # continuity but the new prompt barely lands; lower lets steering
    # through at the cost of continuity.
    ltxv_continuity_strength: float = 1.0
    # Trailing frames of the previous chunk used as the condition.
    ltxv_continuity_frames: int = 9

    audio_enabled: bool = False
    elevenlabs_voice_id: str = ""
    # Minimum continuous silence required between voiceovers — the
    # narrator-clock floor (plan 03). The turn engine only invokes
    # Attenborough once `silence_seconds` reaches this floor, in every
    # mode: with audio the live producer measures real playback time,
    # without audio the engine counts story-time (one turn ≈ one clip).
    min_pause_seconds: float = 5.0

    extra: dict = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
