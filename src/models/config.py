"""Run configuration loaded from YAML.

`configs/text.yaml` plays the film as text; `configs/video.yaml` renders
it on the LTX-2.5 server. Anything a config leaves out takes the default
below.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    # A misspelt or retired setting is an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")

    name: str = "film"                  # the session id's prefix; from_yaml uses the file's name
    llm_backend: str = "openai"         # "openai", or "mock" for the offline tests

    # ── The two roles ──────────────────────────────────────────────
    director_model: str = ""
    showrunner_model: str = ""
    # For a reasoning model (the GPT-5 family): its effort, "none" for the
    # fastest; "" for a chat model.
    director_reasoning_effort: str = ""
    showrunner_reasoning_effort: str = ""
    temperature: float = 0.7            # chat models only
    max_tokens_director: int = 2048
    max_tokens_showrunner: int = 2048

    # ── Video ──────────────────────────────────────────────────────
    # False: the same film plays as text at story pace, with no GPU.
    video_enabled: bool = False
    # The clip length, as the run's one clip shape (`src.i2v.shape`): this
    # many seconds of new picture on top of the frames that repeat the
    # previous clip's tail (6 s → 144 + 9 = 153 frames at 24 fps). Every
    # consumer reads the shape, not this number: the Director's prompt, the
    # story clock, the render request, the player's hold. Under fp8 every
    # frame count compiles on the server for minutes, so a run has one.
    clip_seconds: float = 6.0
    # Startup only: playback starts once `startup_clips + 1` clips are
    # queued, and the queue holds that many. Afterwards the just-in-time
    # schedule keeps `buffer_seconds`.
    startup_clips: int = 0
    # Each render fires its estimate plus this many seconds before its clip
    # is due on air, so a clip is ready about this long before it plays.
    # More absorbs slower renders and Director calls; less lets a viewer's
    # line reach the screen sooner.
    buffer_seconds: float = 4.7
    ltxv_server_url: str = "http://localhost:8000"
    # How hard each clip holds the previous one. 1.0 = maximum visual
    # continuity but a new prompt barely lands; lower lets steering through
    # at the cost of continuity.
    ltxv_continuity_strength: float = 1.0

    # ── The story's pace ───────────────────────────────────────────
    recent_clips: int = 6               # verbatim recent clips in the Director prompt
    max_clips_per_beat: int = 4         # the Director plans one beat of up to this many clips per call
    # A beat that answers a viewer line has at most this many clips: the
    # answer call is on the critical path and its latency tracks its length.
    answer_max_clips: int = 2
    scene_seconds: float = 240.0        # about how long a scene runs; both roles see its running time against it
    compaction_interval: int = 10       # segments between Showrunner compaction passes
    # Fewest segments between two Showrunner runs (the bootstrap is exempt);
    # at most one run is in flight at a time.
    showrunner_min_gap: int = 4
    # A viewer's direction stays active (the scene card paused) until the
    # next line or this many story seconds, whichever comes first.
    viewer_momentum_seconds: float = 90.0

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        data.setdefault("name", Path(path).stem)
        # Where the LTX-2.5 server runs is the machine's business, not the
        # film's: LTXV_SERVER_URL overrides the config's address.
        server_url = os.getenv("LTXV_SERVER_URL", "").strip()
        if server_url:
            data["ltxv_server_url"] = server_url
        return cls(**data)
