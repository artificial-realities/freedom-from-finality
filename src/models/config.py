"""Run configuration loaded from YAML.

`audio_enabled` and `video_enabled` default to False so headless dev
runs stay cheap.
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

    # ── Director engine (graph: "director") ────────────────────────
    # How the narrator's lines are rendered: "ltx" (in the video prompt,
    # native audio) | "tts" (ElevenLabs, mixed over the clip) | "off" (not
    # rendered). A rendering choice only: whether the film has a narrator,
    # how it sounds and any floor of silence between its lines are the
    # story's (`Story.narrator`), and the Director writes the lines either way.
    narration: str = "ltx"
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
