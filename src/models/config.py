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
    # Startup lead, in clips: the consumer waits for `lead_clips + 1` clips
    # to be queued before playback starts, and the queue holds that many.
    # Nothing else reads it: once the player has a clock the just-in-time
    # schedule fires each render as late as it safely can and keeps its
    # own steady-state buffer, so this does not change where a viewer's
    # line lands (two or three clips after it is typed, by arrival time).
    lead_clips: int = 0
    i2v_backend: str = "ltxv"
    i2v_duration: int = 5
    # Fixed clip length in seconds. 0 = `i2v_duration` seconds per clip (the
    # engine's `clip_seconds` property and the runner's hold both read
    # `clip_seconds or i2v_duration`; the compiler carries no duration).
    # N = every clip is exactly N seconds and the Director is told to plan
    # for N: one clip shape, so a compiled server compiles once, pacing is
    # constant, and a viewer's line lands a known number of seconds later.
    clip_seconds: int = 0
    # The reference image the first clip is rendered from. "" → the story's
    # `seed_image` (`stories/<story>/story.json`); set here only for a story
    # without one.
    i2v_seed_image: str = ""

    # LTX-2.5 backend (when i2v_backend == "ltxv"):
    ltxv_server_url: str = ""
    # How hard each chunk holds the previous one. 1.0 = maximum visual
    # continuity but the new prompt barely lands; lower lets steering
    # through at the cost of continuity.
    ltxv_continuity_strength: float = 1.0
    # Trailing frames of the previous chunk used as the condition.
    ltxv_continuity_frames: int = 9
    # How the seed image is fitted to the render frame. A reference within
    # 5 % of the server's aspect ratio is cover-resized (an authored master
    # at the render aspect is the intended case). Outside it, "pad"
    # letterboxes it with a warning (so a square reference is not cropped),
    # "strict" refuses to start, "none" sends the file unchanged. The fill
    # is a colour name, or "auto" to continue the image's own border colour
    # so no seam is introduced.
    i2v_reference_fit: str = "pad"
    i2v_reference_fill: str = "auto"
    # Negative prompt sent with every clip ("" → the server's default). Only
    # the `full` variant encodes it: the distilled recipe runs unguided
    # (guidance_scale 1.0) and the server ignores the field (LTX-08).
    ltxv_negative_prompt: str = ""
    # Every clip's render seed is derived from this key and the clip's
    # segment (`src.i2v.ltxv.derive_seed`), never fixed and never random.
    # "" → the run's session id, so each run has its own noise; set it to
    # an earlier session id to rerun that run's noise (experiment arms
    # share it).
    ltxv_seed_key: str = ""
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
    # OpenAI processing tier for the Director call, the per-clip critical
    # path: "priority" ran gpt-4.1 at ~150 tokens/s instead of ~60 on
    # 2026-09-17 (2.1 s instead of 5.8 s per segment) at a higher per-token
    # price. "" leaves the API default.
    director_service_tier: str = ""
    showrunner_model: str = ""          # override `model` for the Showrunner call
    max_tokens_director: int = 2048
    max_tokens_showrunner: int = 2048
    recent_clips: int = 6               # verbatim recent clips in the Director prompt
    max_clips_per_beat: int = 4         # the Director plans one beat of up to this many clips per call
    # A beat that answers a viewer line has at most this many clips: the
    # answer call is the one on the critical path and its latency tracks
    # its length; the next beat continues the idea. Stated in the user
    # prompt's gates (the system prompt stays cached) and enforced here.
    answer_max_clips: int = 2
    scene_seconds: float = 240.0        # a scene runs about this long; past it the Director is told to close it
    compaction_interval: int = 10       # segments between Showrunner compaction passes
    # Fewest segments between two Showrunner runs (any trigger; the bootstrap
    # is exempt). A trigger that falls inside the gap is noted and looked at
    # again next segment. At most one run is in flight at a time.
    showrunner_min_gap: int = 4
    stagnation_seconds: float = 90.0    # story seconds without structural change before pressure
    # Just-in-time rendering: a clip's render starts this many seconds before
    # the latest moment that would still have it ready on time (the render
    # estimate times a safety factor plus a margin). More slack absorbs
    # slower renders and Director calls; less slack lets a viewer's line
    # reach the screen sooner, because the clip it changes is rendered later.
    render_slack_seconds: float = 3.0
    extra: dict = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
