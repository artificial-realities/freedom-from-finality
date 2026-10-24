"""The clip shape: the one authority on how long a clip is.

A `ClipShape` is a clip's length in frames, fixed before the clip is
planned: the frames the server renders, the frames at its head that
repeat the previous clip's tail (the continuity window), and the frames
the viewer is given. Everything that needs a clip's length reads the
run's shape: Now's prompt, the engine's event record and story
clock, the render request, the player's hold. Nothing derives a length
from seconds anywhere else.

Under fp8 every new frame count compiles on the server for minutes, so a
run has one shape, and the client refuses a server that has not warmed it.

    python -m src.i2v.shape configs/video.yaml   # the frame counts to warm, for LTX2_WARMUP_FRAMES
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DEFAULT_FPS = 24.0
# The frames at the head of every clip that repeat the previous clip's
# tail: the server conditions the clip on them, and the page cuts to the
# clip where they begin, so the viewer sees each frame once.
WINDOW_FRAMES = 9
# Warm-up renders: compilation plus the CPU warm-up of a fresh box cover
# the first three clips of a shape.
WARMUP_REPEATS = 3


def snap_to_frame_grid(raw_frames: int) -> int:
    """A raw frame count on the VAE's causal (8k + 1) grid."""
    return ((max(raw_frames, 1) - 2) // 8 + 1) * 8 + 1


class RendererNotReady(ValueError):
    """The renderer cannot render the run's shape without compiling mid-film,
    or cannot be used at all (unreachable, still loading)."""


class ClipShape(BaseModel):
    """One clip's length in frames: about `seconds` of new picture rendered
    on top of the `overlap_frames` that repeat the previous clip's tail, on
    the 8k + 1 grid; the new frames are delivered (6 s at 24 fps: 153
    rendered, 144 delivered)."""

    model_config = ConfigDict(frozen=True)

    fps: float
    render_frames: int
    overlap_frames: int
    delivered_frames: int

    @classmethod
    def for_seconds(cls, seconds: float, *, fps: float = DEFAULT_FPS,
                    overlap_frames: int = WINDOW_FRAMES) -> "ClipShape":
        render = snap_to_frame_grid(round(seconds * fps) + overlap_frames)
        return cls(fps=fps, render_frames=render, overlap_frames=overlap_frames,
                   delivered_frames=render - overlap_frames)

    @classmethod
    def from_config(cls, config, *, fps: float = DEFAULT_FPS) -> "ClipShape":
        return cls.for_seconds(config.clip_seconds, fps=fps)

    @property
    def delivered_seconds(self) -> float:
        """How long the viewer sees the clip: its hold, and the story clock's tick."""
        return self.delivered_frames / self.fps

    @property
    def render_seconds(self) -> float:
        return self.render_frames / self.fps

    @property
    def overlap_seconds(self) -> float:
        return self.overlap_frames / self.fps

    def record(self) -> dict:
        """The shape as the timing log keeps it."""
        return {"fps": self.fps, "render_frames": self.render_frames, "overlap_frames": self.overlap_frames,
                "delivered_frames": self.delivered_frames, "delivered_seconds": round(self.delivered_seconds, 4)}

    def warmup_frames(self, repeats: int = WARMUP_REPEATS) -> list[int]:
        """The frame counts the server should warm."""
        return [self.render_frames] * repeats

    def check_warm(self, warm_frames) -> None:
        """Refuse a run whose server has not warmed this shape: an unwarmed
        shape compiles mid-film and holds the picture for minutes."""
        if warm_frames is None:
            raise RendererNotReady("the server does not report `warm_frames`; it cannot promise a warm shape")
        warm = {int(f) for f in warm_frames}
        if self.render_frames not in warm:
            raise RendererNotReady(
                f"the server has not warmed [{self.render_frames}] frames (warm: {sorted(warm)}); start it with "
                f"LTX2_WARMUP_FRAMES={','.join(str(f) for f in self.warmup_frames())}")


def seconds_label(seconds: float) -> str:
    """A length as Now reads it: 6.0417 → "6", 4.5 → "4.5"."""
    return f"{seconds:.1f}".rstrip("0").rstrip(".")


def main(argv: list[str]) -> int:
    from src.models.config import Config
    if len(argv) != 1:
        print("usage: python -m src.i2v.shape <config.yaml>", file=sys.stderr)
        return 2
    shape = ClipShape.from_config(Config.from_yaml(Path(argv[0])))
    print(",".join(str(f) for f in shape.warmup_frames()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
