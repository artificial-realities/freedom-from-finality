"""Clip shapes: the one authority on how long a clip is (RUN-01).

A `ClipShape` is a clip's length in frames, fixed before the clip is
planned: the frames the server renders, the frames at its head that
repeat the previous clip's tail (the continuity window), and the frames
the viewer is given. Everything that needs a clip's length reads the
selected shape: the Director's prompt, the engine's event record and
story clock, the render request, the player's hold. Nothing derives a
length from seconds anywhere else.

The `ShapeRegistry` holds the shapes a run may use, built once at start
from the config and the server's frame rate. The production policy
selects the default shape for every clip; extra shapes are registered
(and must be warm on the server, RUN-08) only so an experiment can
select them (X-18) without a second derivation. A frame count the
registry does not hold is refused.

    python -m src.i2v.shape configs/director.live.yaml   # the frame counts to warm, for LTX2_WARMUP_FRAMES
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DEFAULT_FPS = 24.0
# Warm-up renders per shape: compilation plus the CPU warm-up of a fresh
# box cover the first three clips of a shape (2026-09-16 probes).
WARMUP_REPEATS = 3


def snap_to_frame_grid(raw_frames: int) -> int:
    """A raw frame count on the VAE's causal (8k + 1) grid."""
    return ((max(raw_frames, 1) - 2) // 8 + 1) * 8 + 1


class UnregisteredShape(ValueError):
    """A frame count the run's registry does not hold (or a server that has
    not warmed one the registry holds)."""


class ClipShape(BaseModel):
    """One clip's length in frames. With `seam: "fade"` the whole rendered
    file is delivered (the player shows its first `overlap_frames`, which
    repeat the previous clip's tail); with `seam: "overlap"` (LTX-12) the
    render carries the overlap on top of the delivered frames and the
    player cuts where the next clip's repeated head begins."""

    model_config = ConfigDict(frozen=True)

    fps: float
    render_frames: int
    overlap_frames: int
    delivered_frames: int

    @property
    def delivered_seconds(self) -> float:
        """How long the viewer sees the clip: its hold, and the story clock's tick."""
        return self.delivered_frames / self.fps

    @property
    def render_seconds(self) -> float:
        return self.render_frames / self.fps

    @classmethod
    def from_seconds(cls, seconds: float, *, fps: float = DEFAULT_FPS, overlap_frames: int = 0) -> "ClipShape":
        """The shape of a clip of about `seconds`: the frame count on the
        8k + 1 grid at `fps`, all of it delivered."""
        frames = snap_to_frame_grid(round(seconds * fps))
        return cls(fps=fps, render_frames=frames, overlap_frames=overlap_frames, delivered_frames=frames)

    @classmethod
    def overlapped(cls, seconds: float, *, fps: float = DEFAULT_FPS, overlap_frames: int) -> "ClipShape":
        """The overlap seam's shape (LTX-12, D-03): about `seconds` of new
        picture plus the `overlap_frames` that repeat the previous clip's
        tail, rendered on the 8k + 1 grid; the new frames are delivered
        (6 s at 24 fps with a 9-frame window: 153 rendered, 144 delivered)."""
        render = snap_to_frame_grid(round(seconds * fps) + overlap_frames)
        return cls(fps=fps, render_frames=render, overlap_frames=overlap_frames,
                   delivered_frames=render - overlap_frames)

    @property
    def overlap_seconds(self) -> float:
        return self.overlap_frames / self.fps

    def record(self) -> dict:
        """The shape as the timing log and the transcript keep it."""
        return {"fps": self.fps, "render_frames": self.render_frames, "overlap_frames": self.overlap_frames,
                "delivered_frames": self.delivered_frames, "delivered_seconds": round(self.delivered_seconds, 4)}


def seconds_label(seconds: float) -> str:
    """A length as the Director reads it: 6.0417 → "6", 4.5 → "4.5"."""
    return f"{seconds:.1f}".rstrip("0").rstrip(".")


class ShapeRegistry:
    """The shapes a run may use. `default` is the production shape;
    `select` is the production policy (the default for every clip)."""

    def __init__(self, default: ClipShape, extra: list[ClipShape] | None = None) -> None:
        self.default = default
        shapes = {default.render_frames: default}
        for shape in extra or []:
            if shape.fps != default.fps:
                raise ValueError(f"shape of {shape.render_frames} frames at {shape.fps} fps; the run is {default.fps} fps")
            shapes.setdefault(shape.render_frames, shape)
        self._shapes = shapes

    @classmethod
    def from_config(cls, config, *, fps: float = DEFAULT_FPS) -> "ShapeRegistry":
        """The config's `clip_seconds` as the default shape and its
        `extra_clip_seconds` as experiment shapes, at the server's `fps`,
        with the continuity window as the overlap: delivered whole with
        `seam: "fade"`, rendered on top of the delivered frames with
        `seam: "overlap"`."""
        overlap = int(getattr(config, "ltxv_continuity_frames", 0) or 0)
        if getattr(config, "seam", "fade") == "overlap":
            make = lambda s: ClipShape.overlapped(s, fps=fps, overlap_frames=overlap)  # noqa: E731
        else:
            make = lambda s: ClipShape.from_seconds(s, fps=fps, overlap_frames=overlap)  # noqa: E731
        default = make(config.clip_seconds)
        extra = [make(s) for s in getattr(config, "extra_clip_seconds", []) or []]
        return cls(default, extra)

    @property
    def shapes(self) -> list[ClipShape]:
        return [self._shapes[f] for f in sorted(self._shapes)]

    @property
    def frames(self) -> list[int]:
        return sorted(self._shapes)

    def get(self, render_frames: int) -> ClipShape:
        try:
            return self._shapes[render_frames]
        except KeyError:
            raise UnregisteredShape(
                f"no registered clip shape of {render_frames} frames (registered: {self.frames})") from None

    def select(self) -> ClipShape:
        """The shape of the next clip. Production: always the default. A
        variable-length policy is experiment-gated (RUN-02, X-18)."""
        return self.default

    def check_warm(self, warm_frames) -> None:
        """Refuse a run whose server has not warmed every registered shape
        (RUN-08): an unwarmed shape compiles mid-film and holds the picture
        for minutes."""
        if warm_frames is None:
            raise UnregisteredShape("the server does not report `warm_frames`; it cannot promise a warm shape")
        warm = {int(f) for f in warm_frames}
        missing = [f for f in self.frames if f not in warm]
        if missing:
            raise UnregisteredShape(
                f"the server has not warmed {missing} frames (warm: {sorted(warm)}); start it with "
                f"LTX2_WARMUP_FRAMES={','.join(str(f) for f in self.warmup_frames())}")

    def warmup_frames(self, repeats: int = WARMUP_REPEATS) -> list[int]:
        """The frame counts the server should warm, each shape `repeats` times."""
        return [f for f in self.frames for _ in range(repeats)]


def main(argv: list[str]) -> int:
    from src.models.config import Config
    if len(argv) != 1:
        print("usage: python -m src.i2v.shape <config.yaml>", file=sys.stderr)
        return 2
    registry = ShapeRegistry.from_config(Config.from_yaml(Path(argv[0])))
    print(",".join(str(f) for f in registry.warmup_frames()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
