"""LTX-2.5 i2v backend — calls a resident LTX-2.5 inference server.

`server/ltx2_server.py` holds the pipeline in GPU memory and owns the
sliding window: it keeps the tail of each generated chunk in memory and
conditions the next one on it. This backend just sends a prompt and
downloads the returned MP4.

The reference image is uploaded on the first call and seeds the chain;
after that only prompts go over the wire. If the server restarts and
loses its session, the client detects that and re-sends the reference.

LTX-2.5 generates synchronized audio alongside video, so the returned
MP4 already carries an ambient/SFX track.
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

# Generous timeout — the first call can include model download + warmup.
REQUEST_TIMEOUT_S = 600
# Short timeout for the one-off capability probe.
PROBE_TIMEOUT_S = 10

# Fallback when the server can't be probed; LTX-2.5 renders at 24fps.
DEFAULT_FRAME_RATE = 24


class LtxvI2V(I2VBackend):
    """HTTP client for the resident LTX-2.5 server."""

    # The server owns the sliding window, so the runner must not extract
    # and re-feed last frames (see I2VBackend.manages_continuity).
    manages_continuity = True

    def __init__(
        self,
        *,
        server_url: str,
        continuity_strength: float = 1.0,
        continuity_frames: int = 9,
        output_dir: Path | str = "logs/video",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.continuity_strength = continuity_strength
        self.continuity_frames = continuity_frames
        self.model = "ltx-2.5"
        self.resolution = "512x768"
        self.duration = 5  # default seconds when Spielberg doesn't specify
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._seed_image_path: str | None = None
        self._initialized = False
        # Probed from the server on first use so client and server can't
        # silently disagree about the frame rate.
        self._frame_rate: int | None = None

    # ── Frame-count helpers ──────────────────────────────────────────

    @property
    def frame_rate(self) -> int:
        """Server frame rate. Probed once; only a success is cached, so a
        server that wasn't up yet gets re-probed on the next call rather
        than pinning the fallback for the whole session."""
        if self._frame_rate is None:
            self._frame_rate = self._probe_frame_rate()
        return self._frame_rate or DEFAULT_FRAME_RATE

    def _probe_frame_rate(self) -> int | None:
        try:
            resp = requests.get(
                f"{self.server_url}/health", timeout=PROBE_TIMEOUT_S,
            )
            if resp.status_code == 200:
                rate = int(float(resp.json().get("frame_rate", DEFAULT_FRAME_RATE)))
                logger.info("LTX-2.5 server frame rate: %dfps", rate)
                return rate
        except Exception:
            logger.debug("Could not probe server frame rate", exc_info=True)
        return None

    def seconds_to_frames(self, seconds: int) -> int:
        """Seconds → a frame count on the VAE's causal (N*8 + 1) grid."""
        return snap_to_frame_grid(seconds * self.frame_rate)

    # ── Rendering ────────────────────────────────────────────────────

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
            logger.exception("LTX-2.5 render failed on turn %s: %s", turn, exc)
            return None

    def _synthesize_sync(
        self,
        image_path: Path | str,
        prompt: str,
        turn: int,
        duration: int | None = None,
    ) -> str | None:
        # Remember the original seed image so we can re-init after a
        # server restart. Always the reference, never a later frame.
        if self._seed_image_path is None:
            self._seed_image_path = str(image_path)

        payload: dict = {
            "prompt": prompt,
            "continuity_strength": self.continuity_strength,
            "continuity_frames": self.continuity_frames,
            "num_frames": self.seconds_to_frames(duration or self.duration),
        }
        if not self._initialized:
            payload["reference_image"] = _encode_image(self._seed_image_path)

        url = f"{self.server_url}/generate"
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        # Server lost its session (restart / reset) — re-seed and retry once.
        if resp.status_code == 400 and "reference_image" in resp.text:
            logger.warning("LTX-2.5 server lost state — re-seeding reference")
            payload["reference_image"] = _encode_image(self._seed_image_path)
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        if resp.status_code != 200:
            logger.warning(
                "LTX-2.5 server returned %s on turn %s: %s",
                resp.status_code, turn, resp.text[:300],
            )
            return None

        self._initialized = True

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.output_dir / f"turn_{turn:04d}_{stamp}.mp4"
        out_path.write_bytes(resp.content)

        logger.info(
            "LTX-2.5 turn %d rendered in %ss (%ss clip, audio=%s) → %s",
            turn,
            resp.headers.get("X-Latency-S", "?"),
            resp.headers.get("X-Duration-S", "?"),
            resp.headers.get("X-Has-Audio", "?"),
            out_path,
        )
        return str(out_path)


def snap_to_frame_grid(raw_frames: int) -> int:
    """Snap a raw frame count onto the VAE's causal (N*8 + 1) grid."""
    return ((max(raw_frames, 1) - 2) // 8 + 1) * 8 + 1


def _encode_image(path: str) -> str:
    """Read a local image and return its base64 encoding."""
    return base64.b64encode(Path(path).read_bytes()).decode()
