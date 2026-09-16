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
    graph: str = "director"
    llm_backend: str  # "openai"
    model: str
    temperature: float = 0.7
    max_tokens_per_agent: int = 1024

    # When True, dispatch to the continuous live loop (run_live) instead
    # of the turn-by-turn `run_play`. With video disabled the film plays
    # as text at story pace (useful with the web player).
    live: bool = False

    video_enabled: bool = False
    # Producer lead, in clips. The live producer renders ahead of the
    # player by this many clips and the consumer waits for `lead_clips + 1`
    # clips to be queued before starting playback. 0 = no startup wait,
    # no enforced race-ahead (queue size 1, render and play overlap by one
    # clip naturally). Bump above 0 to re-enable sustained
    # "delay-as-feature" narrative smoothing once i2v latency drops.
    lead_clips: int = 0
    i2v_backend: str = "ltxv"
    i2v_duration: int = 5
    # Fixed clip length in seconds. 0 = each segment renders for the sum of
    # the Director's shot durations (3-15 s). N = every clip is exactly N
    # seconds and the Director is told to plan for N: one clip shape, so a
    # compiled server compiles once, pacing is constant, and a viewer's
    # line lands a known number of seconds later.
    clip_seconds: int = 0
    i2v_seed_image: str = "data/legoman.png"

    # LTX-2.5 backend (when i2v_backend == "ltxv"):
    ltxv_server_url: str = ""
    # How hard each chunk holds the previous one. 1.0 = maximum visual
    # continuity but the new prompt barely lands; lower lets steering
    # through at the cost of continuity.
    ltxv_continuity_strength: float = 1.0
    # Trailing frames of the previous chunk used as the condition.
    ltxv_continuity_frames: int = 9
    # How the seed image is fitted to the render frame: "pad" letterboxes it
    # at the server's aspect ratio (so a square reference is not cropped),
    # "none" sends it unchanged. The fill is a colour name, or "auto" to
    # continue the image's own border colour so no seam is introduced.
    i2v_reference_fit: str = "pad"
    i2v_reference_fill: str = "auto"
    # Negative prompt sent with every clip ("" → the server's default) and an
    # optional fixed seed for reproducible renders.
    ltxv_negative_prompt: str = ""
    ltxv_seed: int | None = None
    # Identity anchor: the server also conditions on the reference image as
    # a guiding keyframe at `ltxv_anchor_index` (latent frames, -1 = the
    # last) with this strength, so every clip has to return to the sheet.
    # 0.0 = off. diffusers' long-form recipe uses 0.95.
    ltxv_anchor_strength: float = 0.0
    ltxv_anchor_index: int = -1
    # Replay backend (i2v_backend == "replay"): pre-rendered clips served
    # in order from a directory, for developing the player without a GPU.
    i2v_replay_dir: str = ""
    i2v_replay_delay: float = 0.0

    audio_enabled: bool = False
    elevenlabs_voice_id: str = ""
    # Minimum continuous silence required between voice-over lines. The
    # Director may only fill `voiceover` once `silence_seconds` reaches
    # this floor.
    min_pause_seconds: float = 5.0

    # ── Director engine (graph: "director") ────────────────────────
    narration: str = "ltx"              # "ltx" (native audio prompt) | "tts" (ElevenLabs) | "off"
    voiceover_style: str = "warm, unhurried documentary narrator"
    director_model: str = ""            # override `model` for the Director call
    showrunner_model: str = ""          # override `model` for the Showrunner call
    max_tokens_director: int = 2048
    max_tokens_showrunner: int = 2048
    recent_beats: int = 4               # verbatim recent segments in the Director prompt
    max_shots_per_segment: int = 3
    max_segments_per_scene: int = 12    # hard cap; forces scene_done → Showrunner replan
    compaction_interval: int = 10       # segments between Showrunner compaction passes
    stagnation_threshold: int = 5       # segments without structural change before pressure
    extra: dict = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
