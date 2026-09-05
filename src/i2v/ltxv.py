"""LTXV i2v backend — calls a resident LTX inference server.

The server (server/ltx2_server.py for LTX-2.5, or server/ltxv_server.py
for LTX-Video 13b) holds the pipeline in GPU memory and manages
sliding-window state internally. This backend just sends prompts and
downloads the returned MP4.

On the first call the reference/anchor image is uploaded. Subsequent
calls send only the prompt — the server conditions on its own sliding
window + the stored anchor.

LTX-2.5 generates native audio alongside video — the returned MP4
includes synchronized ambient sound/effects automatically.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime
from pathlib import Path

import requests

from src.i2v.base import I2VBackend

logger = logging.getLogger(__name__)

# Generous timeout — first call may include model download + warmup.
REQUEST_TIMEOUT_S = 300


class LtxvI2V(I2VBackend):
    # LTX-2.5 renders at 24fps; the pipeline requires (N*8 + 1) frames.
    FRAME_RATE = 24

    def __init__(
        self,
        *,
        server_url: str,
        anchor_strength: float = 0.5,
        output_dir: Path | str = "logs/video",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.anchor_strength = anchor_strength
        self.model = "ltx-2.5"
        self.resolution = "512x768"
        self.duration = 5  # default seconds if Spielberg doesn't specify
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._seed_image_path: str | None = None
        self._initialized = False

    @staticmethod
    def seconds_to_frames(seconds: int) -> int:
        """Convert a duration in seconds to the nearest valid frame count.

        The LTXV pipeline requires frame counts of the form (N*8 + 1).
        """
        raw = seconds * LtxvI2V.FRAME_RATE
        # Round to nearest (N*8 + 1).
        return ((raw - 2) // 8 + 1) * 8 + 1

    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
        duration: int | None = None,
    ) -> str | None:
        try:
            return await asyncio.to_thread(
                self._synthesize_sync, image_path, prompt, turn, duration,
            )
        except Exception as exc:
            logger.exception("LTXV render failed on turn %s: %s", turn, exc)
            return None

    def _synthesize_sync(
        self,
        image_path: Path | str,
        prompt: str,
        turn: int,
        duration: int | None = None,
    ) -> str | None:
        # Remember the original seed image for re-init after server restart.
        if self._seed_image_path is None:
            self._seed_image_path = str(image_path)

        num_frames = self.seconds_to_frames(duration or self.duration)

        payload: dict = {
            "prompt": prompt,
            "anchor_strength": self.anchor_strength,
            "num_frames": num_frames,
        }

        if not self._initialized:
            payload["reference_image"] = _encode_image(self._seed_image_path)

        url = f"{self.server_url}/generate"
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        # Server lost state (restart, reset) — re-send reference and retry.
        if resp.status_code == 400 and "reference_image" in resp.text:
            logger.warning("LTXV server lost state — re-initialising")
            payload["reference_image"] = _encode_image(self._seed_image_path)
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        if resp.status_code != 200:
            logger.warning(
                "LTXV server returned %s on turn %s: %s",
                resp.status_code,
                turn,
                resp.text[:300],
            )
            return None

        self._initialized = True

        # Save the returned MP4.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.output_dir / f"turn_{turn:04d}_{stamp}.mp4"
        out_path.write_bytes(resp.content)

        latency = resp.headers.get("X-Latency-S", "?")
        logger.info("LTXV turn %d rendered in %ss → %s", turn, latency, out_path)
        return str(out_path)


def _encode_image(path: str) -> str:
    """Read a local image and return its base64 encoding."""
    return base64.b64encode(Path(path).read_bytes()).decode()
