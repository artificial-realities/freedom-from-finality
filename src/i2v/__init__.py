from src.i2v.base import I2VBackend
from src.i2v.ltxv import LtxvI2V
from src.i2v.replay import ReplayI2V

__all__ = [
    "I2VBackend",
    "LtxvI2V",
    "ReplayI2V",
    "build_i2v_backend",
]


def build_i2v_backend(
    name: str,
    *,
    server_url: str | None = None,
    continuity_strength: float = 1.0,
    continuity_frames: int = 9,
    output_dir=None,
    reference_fit: str = "pad",
    reference_fill: str = "auto",
    negative_prompt: str = "",
    seed=None,
    replay_dir: str = "",
    replay_delay: float = 0.0,
    **_extra,
) -> I2VBackend:
    """Construct an I2V backend from a config's `i2v_backend` string."""
    key = name.lower()
    if key == "ltxv":
        if not server_url:
            raise ValueError(
                "i2v_backend='ltxv' requires ltxv_server_url in config"
            )
        return LtxvI2V(
            server_url=server_url,
            continuity_strength=continuity_strength,
            continuity_frames=continuity_frames,
            output_dir=output_dir or "logs/video",
            reference_fit=reference_fit,
            reference_fill=reference_fill,
            negative_prompt=negative_prompt,
            seed=seed,
        )
    if key == "replay":
        if not replay_dir:
            raise ValueError("i2v_backend='replay' requires i2v_replay_dir in config")
        return ReplayI2V(
            clip_dir=replay_dir, output_dir=output_dir or "logs/video", delay=replay_delay,
        )
    raise ValueError(f"Unknown i2v_backend: {name!r}")
