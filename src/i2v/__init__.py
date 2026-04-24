from src.i2v.base import I2VBackend
from src.i2v.dashscope import DashScopeI2V
from src.i2v.frame_extractor import extract_last_frame

__all__ = ["DashScopeI2V", "I2VBackend", "build_i2v_backend", "extract_last_frame"]


def build_i2v_backend(
    name: str,
    *,
    model: str,
    resolution: str = "480P",
    duration: int = 5,
    output_dir=None,
) -> I2VBackend:
    """Construct an I2V backend from a config's `i2v_backend` string."""
    key = name.lower()
    if key == "dashscope":
        return DashScopeI2V(
            model=model,
            resolution=resolution,
            duration=duration,
            output_dir=output_dir or "logs/video",
        )
    raise ValueError(f"Unknown i2v_backend: {name!r}")
