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
import io
import logging
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

from src.i2v.base import I2VBackend

logger = logging.getLogger(__name__)

# Generous timeout — the first call can include model download + warmup.
REQUEST_TIMEOUT_S = 600
# Short timeout for the one-off capability probe.
PROBE_TIMEOUT_S = 10

# Fallbacks when the server can't be probed: LTX-2.5 renders at 24fps and
# the server defaults to 768x512.
DEFAULT_FRAME_RATE = 24
DEFAULT_SIZE = (768, 512)
# Fraction of the frame height the reference subject is scaled to when
# letterboxed: a wide product shot with margins, not a crop.
REFERENCE_SCALE = 0.8


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
        reference_fit: str = "pad",
        reference_fill: str = "auto",
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.continuity_strength = continuity_strength
        self.continuity_frames = continuity_frames
        # "pad": letterbox the seed image onto a `reference_fill` canvas at
        # the server's render size so nothing is cropped (a square reference
        # sent to a 3:2 render otherwise loses the top of the subject and the
        # model invents a new head). "none": send the file as-is.
        self.reference_fit = reference_fit
        self.reference_fill = reference_fill
        self.negative_prompt = negative_prompt  # "" → the server's default
        self.seed = seed                        # None → random per clip
        self.model = "ltx-2.5"
        self.resolution = "512x768"
        self.duration = 5  # default seconds when the segment doesn't specify
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._seed_image_path: str | None = None
        self._initialized = False
        # Probed from the server on first use so client and server can't
        # silently disagree about the frame rate or the render size.
        self._frame_rate: int | None = None
        self._size: tuple[int, int] | None = None

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
        health = self._probe_health()
        if not health:
            return None
        rate = int(float(health.get("frame_rate", DEFAULT_FRAME_RATE)))
        logger.info("LTX-2.5 server frame rate: %dfps", rate)
        return rate

    @property
    def size(self) -> tuple[int, int]:
        """Server render size (width, height); probed once."""
        if self._size is None:
            health = self._probe_health()
            if health and health.get("width") and health.get("height"):
                self._size = (int(health["width"]), int(health["height"]))
        return self._size or DEFAULT_SIZE

    def _probe_health(self) -> dict | None:
        try:
            resp = requests.get(f"{self.server_url}/health", timeout=PROBE_TIMEOUT_S)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            logger.debug("Could not probe server health", exc_info=True)
        return None

    def _encode_reference(self, path: str) -> str:
        if self.reference_fit == "pad":
            width, height = self.size
            return base64.b64encode(
                letterbox_reference(path, width, height, fill=self.reference_fill),
            ).decode()
        return _encode_image(path)

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
        reset: bool = False,
    ) -> str | None:
        try:
            return await asyncio.to_thread(
                self._synthesize_sync, image_path, prompt, turn, duration, reset,
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
        reset: bool = False,
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
            # A cut: the server conditions on the reference image instead of
            # the sliding window, which bounds identity drift.
            "reset_window": reset,
        }
        if self.negative_prompt:
            payload["negative_prompt"] = self.negative_prompt
        if self.seed is not None:
            payload["seed"] = self.seed
        if not self._initialized:
            payload["reference_image"] = self._encode_reference(self._seed_image_path)

        url = f"{self.server_url}/generate"
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        # Server lost its session (restart / reset) — re-seed and retry once.
        if resp.status_code == 400 and "reference_image" in resp.text:
            logger.warning("LTX-2.5 server lost state — re-seeding reference")
            payload["reference_image"] = self._encode_reference(self._seed_image_path)
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


def border_color(img: Image.Image, band: int = 4) -> tuple[int, int, int]:
    """Median colour of the outer `band` pixels: the background the image
    itself was shot on, so padding continues it instead of adding a seam."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    samples = [px[x, y] for y in range(h) for x in range(w)
               if x < band or y < band or x >= w - band or y >= h - band]
    channels = list(zip(*samples))
    return tuple(sorted(c)[len(c) // 2] for c in channels)


def letterbox_reference(
    path: Path | str, width: int, height: int, *, fill: str = "auto",
    scale: float = REFERENCE_SCALE,
) -> bytes:
    """Fit a reference image into a `width`x`height` frame without cropping.

    The subject is scaled to `scale` of the frame height (or width, if it
    is wider than the frame allows) and centred on a canvas. `fill` is a
    colour name, or "auto" to continue the image's own border colour, which
    avoids a visible rectangle the video model would render as a picture
    frame. Transparent images are composited onto the fill. Returns PNG bytes.
    """
    img = Image.open(path).convert("RGBA")
    if fill == "auto":
        fill = border_color(img)
    img = Image.alpha_composite(Image.new("RGBA", img.size, fill), img).convert("RGB")
    ratio = (height * scale) / img.height
    if img.width * ratio > width * scale:
        ratio = (width * scale) / img.width
    new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
    img = img.resize(new_size, Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), fill)
    canvas.paste(img, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
