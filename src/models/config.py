"""Run configuration loaded from YAML.

`video_enabled` defaults to False so headless dev runs stay cheap.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


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
    # Startup only, in clips: the consumer waits for `startup_clips + 1`
    # clips to be queued before playback starts, and the queue holds that
    # many. Once the player has a clock the just-in-time schedule keeps
    # `buffer_seconds` instead, so this does not change where a viewer's
    # line lands (two or three clips after it is typed, by arrival time).
    startup_clips: int = 0
    i2v_backend: str = "ltxv"
    # The clip length, as the run's one production clip shape
    # (`src.i2v.shape`): this many seconds on the 8k + 1 frame grid at the
    # server's frame rate (6 s → 145 frames at 24 fps, 6.04 s on air). Every
    # consumer reads the shape, not this number: the Director's prompt, the
    # story clock, the render request, the player's hold. One shape, so a
    # compiled server compiles once and a line lands a known time later.
    clip_seconds: float = 6.0
    # How clips meet. "fade": each file plays
    # whole, its first `ltxv_continuity_frames` repeating the previous clip's
    # tail, and the page crossfades the elements' opacity. "overlap": the
    # server renders the clip's seconds plus the window (6 s → 144 + 9 = 153
    # frames) and the viewer is given the 144 new ones: the page cuts to the
    # next clip where its repeated head begins (no opacity fade) and
    # crossfades the sound over it; hold, story clock and Director read 6.0 s.
    # The live config uses "overlap" with the server's loudness ceiling.
    seam: str = "fade"
    # The server's loudness ceiling for the run: LUFS, with the audio
    # padded to the video and kept under -1 dBTP; None = off. `vast_run.sh`
    # passes it to the server as LTX2_AUDIO_CEILING_LUFS; the client warns
    # when the server's differs. The live config uses -18 with the overlap
    # seam, so the sound crossfades between clips of the same loudness.
    audio_ceiling_lufs: float | None = None

    # LTX-2.5 backend (when i2v_backend == "ltxv"):
    ltxv_server_url: str = ""
    # How hard each chunk holds the previous one. 1.0 = maximum visual
    # continuity but the new prompt barely lands; lower lets steering
    # through at the cost of continuity.
    ltxv_continuity_strength: float = 1.0
    # Trailing frames of the previous chunk used as the condition.
    ltxv_continuity_frames: int = 9
    # After a server restart: "window" (default) sends the last clip's tail
    # frames so the restarted server continues the picture (the scene
    # survives, about 0.4 s more per render); "reference" re-seeds from the reference
    # image, which resets costume and position. The reference is used anyway
    # before the first clip, or when the tail cannot be read (the Director is
    # then told the picture was reset).
    ltxv_restart_recovery: str = "window"
    # How the seed image is fitted to the render frame. "strict" (one
    # standard size for render and reference) takes only a reference at the render aspect
    # to the pixel and resizes it whole, and refuses to start otherwise.
    # "pad": a reference within 5 % of the server's aspect is cover-resized,
    # one outside it letterboxed with a warning (so a square reference is
    # not cropped). "none" sends the file unchanged. The fill is a colour
    # name, or "auto" to continue the image's own border colour so no seam
    # is introduced.
    i2v_reference_fit: str = "pad"
    i2v_reference_fill: str = "auto"
    # Negative prompt sent with every clip ("" → the server's default). Only
    # the `full` variant encodes it: the distilled recipe runs unguided
    # (guidance_scale 1.0) and the server ignores the field.
    ltxv_negative_prompt: str = ""
    # Every clip's render seed is derived from this key and the clip's
    # segment (`src.i2v.ltxv.derive_seed`), never fixed and never random.
    # "" → the run's session id, so each run has its own noise; set it to
    # an earlier session id to rerun that run's noise.
    ltxv_seed_key: str = ""
    # Identity anchor: the server also conditions on the reference image as
    # a guiding keyframe at `ltxv_anchor_index` (latent frames, -1 = the
    # last) with this strength, so every clip has to return to the sheet.
    # 0.0 = off: on the distilled recipe identity holds without it, an end
    # anchor suppresses the closing action and a mid anchor inserts an idle
    # beat. diffusers' long-form recipe uses 0.95.
    ltxv_anchor_strength: float = 0.0
    ltxv_anchor_index: int = -1

    # ── Director engine (graph: "director") ────────────────────────
    director_model: str = ""            # override `model` for the Director call
    # OpenAI processing tier for the Director call, the per-clip critical
    # path: "priority" runs about twice as many tokens per second at a
    # higher per-token price. "" leaves the API default.
    director_service_tier: str = ""
    # For a reasoning model (the GPT-5 family): its effort, "none" for the
    # fastest; "" for a chat model.
    director_reasoning_effort: str = ""
    showrunner_model: str = ""          # override `model` for the Showrunner call
    showrunner_reasoning_effort: str = ""
    max_tokens_director: int = 2048
    max_tokens_showrunner: int = 2048
    recent_clips: int = 6               # verbatim recent clips in the Director prompt
    max_clips_per_beat: int = 4         # the Director plans one beat of up to this many clips per call
    # A beat that answers a viewer line has at most this many clips: the
    # answer call is the one on the critical path and its latency tracks
    # its length; the next beat continues the idea. Stated in the user
    # prompt's viewer section (the system prompt stays cached) and enforced here.
    answer_max_clips: int = 2
    scene_seconds: float = 240.0        # about how long a scene runs; both roles see the scene's running time against it
    compaction_interval: int = 10       # segments between Showrunner compaction passes
    # Fewest segments between two Showrunner runs (any trigger; the bootstrap
    # is exempt). A trigger that falls inside the gap is noted and looked at
    # again next segment. At most one run is in flight at a time.
    showrunner_min_gap: int = 4
    # A viewer's direction stays active (shaping the Director's beats, the
    # scene card paused) until the next line or this many story seconds,
    # whichever comes first.
    viewer_momentum_seconds: float = 90.0
    # The steady-state buffer the just-in-time schedule keeps: each
    # render fires the render estimate plus this many seconds before its
    # clip is due on air, so a clip is ready about this long before it
    # plays. More absorbs slower renders and Director calls that answer a
    # line late; less lets a line reach the screen sooner, because the clip
    # it changes is rendered later. 4.7 s has played without a stall; a
    # faster Director may allow less, which is not yet verified live.
    buffer_seconds: float = 4.7
    # When a beat has no clip left, the Director call for the
    # next beat starts at once instead of one estimated call (x JIT_SAFETY,
    # plus a margin) before the next render's fire; a viewer's line during
    # the call still cancels and restarts it. With a slow Director the two
    # moments nearly coincide (the estimate is about a clip), so it gains
    # little; with a fast one it keeps the queue fuller and lines land
    # sooner. False: the call starts just before it is needed.
    plan_early: bool = True
    # Fail-soft: a failed render is retried once when the buffer
    # allows; otherwise, or when the retry fails, the world and the beat are
    # restored to before the failed clip and the Director replans from the
    # last clip the viewer received. An exception while emitting a clip takes
    # the same route instead of ending the film. False: a failed render is
    # skipped and an emit exception ends the film.
    fail_soft: bool = True
    extra: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_seam(self):
        if self.seam not in ("fade", "overlap"):
            raise ValueError(f"seam must be 'fade' or 'overlap', not {self.seam!r}")
        if self.ltxv_restart_recovery not in ("reference", "window"):
            raise ValueError(f"ltxv_restart_recovery must be 'reference' or 'window', not {self.ltxv_restart_recovery!r}")
        return self

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        # Where the LTX-2.5 server runs is the machine's business, not the
        # film's: LTXV_SERVER_URL overrides the config's address (a container,
        # a server on another host) without editing the config.
        server_url = os.getenv("LTXV_SERVER_URL", "").strip()
        if server_url:
            data["ltxv_server_url"] = server_url
        return cls(**data)
