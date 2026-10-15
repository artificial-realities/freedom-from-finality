"""Run configuration loaded from YAML.

`video_enabled` defaults to False so headless dev runs stay cheap.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


# What the pre-RUN-03 schedule kept on top of `render_slack_seconds`, at the
# baseline's 4.8 s render: 0.25 x 4.8 s of safety surplus and a 0.5 s margin.
LEGACY_SLACK_TO_BUFFER = 1.7


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
    # Was `lead_clips` (RUN-03).
    startup_clips: int = 0
    i2v_backend: str = "ltxv"
    # The clip length, as the run's one production clip shape
    # (`src.i2v.shape`): this many seconds on the 8k + 1 frame grid at the
    # server's frame rate (6 s → 145 frames at 24 fps, 6.04 s on air). Every
    # consumer reads the shape, not this number: the Director's prompt, the
    # story clock, the render request, the player's hold. One shape, so a
    # compiled server compiles once and a line lands a known time later.
    clip_seconds: float = 6.0
    # More shapes, registered (and required warm on the server) so an
    # experiment can select them (X-18); the production policy never does.
    extra_clip_seconds: list[float] = Field(default_factory=list)
    # How clips meet (LTX-12 / AUD-02 / AUD-03, D-03). "fade": each file plays
    # whole, its first `ltxv_continuity_frames` repeating the previous clip's
    # tail, and the page crossfades the elements' opacity. "overlap": the
    # server renders the clip's seconds plus the window (6 s → 144 + 9 = 153
    # frames) and the viewer is given the 144 new ones: the page cuts to the
    # next clip where its repeated head begins (no opacity fade) and
    # crossfades the sound over it; hold, story clock and Director read 6.0 s.
    # Rolled out with the server's loudness ceiling after RUN-14's GPU check.
    seam: str = "fade"
    # The server's loudness ceiling for the run (AUD-03): LUFS, with the audio
    # padded to the video and kept under -1 dBTP; None = off. `vast_run.sh`
    # passes it to the server as LTX2_AUDIO_CEILING_LUFS; the client warns
    # when the server's differs. D-03: -18 with `seam: "overlap"`.
    audio_ceiling_lufs: float | None = None

    # LTX-2.5 backend (when i2v_backend == "ltxv"):
    ltxv_server_url: str = ""
    # How hard each chunk holds the previous one. 1.0 = maximum visual
    # continuity but the new prompt barely lands; lower lets steering
    # through at the cost of continuity.
    ltxv_continuity_strength: float = 1.0
    # Trailing frames of the previous chunk used as the condition.
    ltxv_continuity_frames: int = 9
    # After a server restart (RUN-10, D-12): "window" (default) sends the last
    # clip's tail frames so the restarted server continues the picture (X-11:
    # the scene survives, +0.4 s); "reference" re-seeds from the reference
    # image, which resets costume and position. The reference is used anyway
    # before the first clip, or when the tail cannot be read (the Director is
    # then told the picture was reset).
    ltxv_restart_recovery: str = "window"
    # How the seed image is fitted to the render frame. "strict" (RUN-17,
    # D-15: one standard size) takes only a reference at the render aspect
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

    # ── Director engine (graph: "director") ────────────────────────
    director_model: str = ""            # override `model` for the Director call
    # OpenAI processing tier for the Director call, the per-clip critical
    # path: "priority" ran gpt-4.1 at ~150 tokens/s instead of ~60 on
    # 2026-09-17 (2.1 s instead of 5.8 s per segment) at a higher per-token
    # price. "" leaves the API default.
    director_service_tier: str = ""
    # RUN-16: the Director's model is a reasoning model (the GPT-5 family):
    # its effort, "none" for the fastest; "" for a chat model (gpt-4.1).
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
    # prompt's gates (the system prompt stays cached) and enforced here.
    answer_max_clips: int = 2
    scene_seconds: float = 240.0        # a scene runs about this long; past it the Director is told to close it
    compaction_interval: int = 10       # segments between Showrunner compaction passes
    # Fewest segments between two Showrunner runs (any trigger; the bootstrap
    # is exempt). A trigger that falls inside the gap is noted and looked at
    # again next segment. At most one run is in flight at a time.
    showrunner_min_gap: int = 4
    stagnation_seconds: float = 90.0    # story seconds without structural change before pressure
    # ARCH-08: a viewer's direction stays active (shaping the Director's beats,
    # the card's candidate beats hidden, ARCH-09) until the next line or this
    # many story seconds, whichever comes first.
    viewer_momentum_seconds: float = 90.0
    # The steady-state buffer the just-in-time schedule keeps (RUN-03): each
    # render fires the render estimate plus this many seconds before its
    # clip is due on air, so a clip is ready about this long before it
    # plays. More absorbs slower renders and Director calls that answer a
    # line late; less lets a line reach the screen sooner, because the clip
    # it changes is rendered later. 4.7 s is what the schedule kept
    # implicitly before it was named (a 1.25x render estimate, 0.5 s and
    # 3.0 s of slack, at the baseline's 4.8 s render). Lowering it waits for
    # X-09 (RUN-06).
    buffer_seconds: float = 4.7
    # RUN-15 (D-14): when a beat has no clip left, the Director call for the
    # next beat starts at once instead of one estimated call (x JIT_SAFETY,
    # plus a margin) before the next render's fire; a viewer's line during
    # the call still cancels and restarts it. With a slow Director the two
    # moments nearly coincide (the estimate is about a clip), so it gains
    # little; with a fast one it keeps the queue fuller and lines land
    # sooner (BATCH_033). False: the old schedule.
    plan_early: bool = True
    # The just-in-time policy. "current" is production. "deadline" is RUN-06's
    # candidate, for the X-09 experiment only (RUN-06 is EXPERIMENT_REQUIRED;
    # do not enable it in a production config): render and Director
    # estimates as the rolling p95 of the last 20, floored by the runtime
    # baseline, and deadline-aware line admission (a line that the Director
    # cannot answer before the next fire plus 1 s is answered from the clip
    # after it instead of spending the buffer).
    jit_policy: str = "current"
    # Fail-soft (RUN-09): a failed render is retried once when the buffer
    # allows; otherwise, or when the retry fails, the world and the beat are
    # restored to before the failed clip and the Director replans from the
    # last clip the viewer received. An exception while emitting a clip takes
    # the same route instead of ending the film. False = the old behaviour
    # (a failed render is skipped, an emit exception ends the film).
    fail_soft: bool = True
    extra: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _one_duration(cls, data):
        """`i2v_duration` (before RUN-01) maps once onto `clip_seconds`, where
        it was the length used when `clip_seconds` was 0, and then goes."""
        if isinstance(data, dict) and "i2v_duration" in data:
            data = dict(data)
            legacy = data.pop("i2v_duration")
            if not data.get("clip_seconds"):
                data["clip_seconds"] = legacy
        return data

    @model_validator(mode="before")
    @classmethod
    def _startup_and_buffer(cls, data):
        """Before RUN-03: `lead_clips` is `startup_clips`; `render_slack_seconds`
        was added to 1.25x the render estimate and a 0.5 s margin, which at
        the baseline's 4.8 s render is a buffer of the slack plus 1.7 s."""
        if isinstance(data, dict) and ("lead_clips" in data or "render_slack_seconds" in data):
            data = dict(data)
            if "lead_clips" in data:
                data.setdefault("startup_clips", data.pop("lead_clips"))
            if "render_slack_seconds" in data:
                slack = data.pop("render_slack_seconds")
                data.setdefault("buffer_seconds", slack + LEGACY_SLACK_TO_BUFFER)
        return data

    @model_validator(mode="after")
    def _known_seam(self):
        if self.seam not in ("fade", "overlap"):
            raise ValueError(f"seam must be 'fade' or 'overlap', not {self.seam!r}")
        if self.ltxv_restart_recovery not in ("reference", "window"):
            raise ValueError(f"ltxv_restart_recovery must be 'reference' or 'window', not {self.ltxv_restart_recovery!r}")
        return self

    @model_validator(mode="before")
    @classmethod
    def _one_compiler(cls, data):
        """`compiler` chose between `src/story/compiler.py` and LTX-01's
        caption-prose arm; X-05 rejected the arm (2026-09-23) and it was
        removed, so the key goes, and asking for the arm is an error."""
        if isinstance(data, dict) and "compiler" in data:
            data = dict(data)
            if data.pop("compiler") not in ("labels", None):
                raise ValueError("the prose compiler was removed (LTX-01 rejected after X-05); drop `compiler`")
        return data

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
