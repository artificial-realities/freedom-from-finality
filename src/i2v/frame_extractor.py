"""Last-frame extraction — bridges one i2v clip to the next.

Each rendered clip's last frame becomes the seed image for the next
render. Lifted from notebooks/i2v_chaining_test.ipynb.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_last_frame(video_path: Path | str, output_path: Path | str) -> str | None:
    """Save the last frame of `video_path` as a PNG at `output_path`.

    Returns the output path on success, or None if the video can't be
    opened or read. Uses cv2 lazily so headless installs without
    OpenCV still import this module cleanly.
    """
    try:
        import cv2  # noqa: WPS433
    except ImportError:
        logger.exception("cv2 not installed — `pip install opencv-python-headless`")
        return None

    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning("Could not open video for frame extraction: %s", video_path)
        return None
    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            logger.warning("Video has zero frames: %s", video_path)
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)
        ret, frame = cap.read()
        if not ret or frame is None:
            logger.warning("Failed to read last frame from %s", video_path)
            return None
        if not cv2.imwrite(str(output_path), frame):
            logger.warning("Failed to write last frame to %s", output_path)
            return None
        return str(output_path)
    finally:
        cap.release()
