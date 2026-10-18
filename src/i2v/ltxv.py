"""LTX-2.5 backend: the HTTP client of the resident LTX-2.5 server.

`server/ltx2_server.py` holds the pipeline in GPU memory and owns the
sliding window: it keeps the tail of each generated clip in memory and
conditions the next one on it. This backend sends a prompt and downloads
the returned MP4, which already carries its sound.

The reference image is uploaded on the first call and seeds the chain;
after that only prompts go over the wire. If the server restarts and
loses its session, the client sends the reference again together with the
last clip's tail, so the picture continues.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

from src.i2v.base import I2VBackend
from src.i2v.shape import WINDOW_FRAMES, ClipShape, RendererNotReady

logger = logging.getLogger(__name__)

# Generous timeout: the first call can include a compile.
REQUEST_TIMEOUT_S = 600
# Short timeout for the one-off capability probe.
PROBE_TIMEOUT_S = 10

# Fallbacks when the server can't be probed.
DEFAULT_FRAME_RATE = 24.0
DEFAULT_SIZE = (512, 288)
# The server's per-clip response headers, as `last_render` keys for the
# run's timing log.
RENDER_HEADERS: dict[str, tuple[str, type]] = {
    "X-Latency-S": ("latency_s", float),
    "X-Encode-S": ("encode_s", float),
    "X-Post-S": ("post_s", float),       # frames to uint8 after the pipeline
    "X-Num-Frames": ("num_frames", int),
    "X-Duration-S": ("duration_s", float),
    "X-Frame-Rate": ("frame_rate", float),
    "X-Has-Audio": ("has_audio", str),
    "X-Session-Epoch": ("session_epoch", str),   # a new value after a server restart
    "X-Audio-LUFS": ("audio_lufs", float),
    "X-Audio-TP": ("audio_tp", float),
    "X-Audio-Gain-dB": ("audio_gain_db", float),
}


class LtxvI2V(I2VBackend):
    """HTTP client for the resident LTX-2.5 server."""

    def __init__(
        self,
        *,
        server_url: str,
        continuity_strength: float = 1.0,
        output_dir: Path | str = "logs/video",
        seed_key: str = "",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self._last_clip: str | None = None
        self.continuity_strength = continuity_strength
        self.continuity_frames = WINDOW_FRAMES
        # Every clip's seed is derived from this key (the session id) and its
        # segment (`derive_seed`); "" → the server draws one.
        self.seed_key = seed_key
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._seed_image_path: str | None = None
        self._initialized = False
        # Probed from the server on first use so client and server can't
        # silently disagree about the frame rate or the render size.
        self._frame_rate: float | None = None
        self._size: tuple[int, int] | None = None

    def _tail_frames(self, path: str, count: int) -> list[str]:
        try:
            return _tail_frames_png(path, count, self.size)
        except Exception:
            logger.exception("Could not read the last clip's tail from %s; re-seeding from the reference", path)
            return []

    # ── Frame-count helpers ──────────────────────────────────────────

    @property
    def frame_rate(self) -> float:
        """Server frame rate. Probed once; only a success is cached, so a
        server that wasn't up yet gets re-probed on the next call rather
        than pinning the fallback for the whole session."""
        if self._frame_rate is None:
            self._frame_rate = self._probe_frame_rate()
        return self._frame_rate or DEFAULT_FRAME_RATE

    def _probe_frame_rate(self) -> float | None:
        health = self._probe_health()
        if not health:
            return None
        rate = float(health.get("frame_rate", DEFAULT_FRAME_RATE))
        logger.info("LTX-2.5 server frame rate: %gfps", rate)
        return rate

    def check_shape(self, shape: ClipShape) -> dict:
        """The server must report the run's shape among the frame counts it
        warmed (`/health` `warm_frames`), at the frame rate the shape was
        built for; otherwise the run is refused before it starts, not
        stalled for minutes mid-film by a compile."""
        health = self._probe_health()
        if health is None:
            raise RendererNotReady(
                f"the LTX-2.5 server at {self.server_url} is not reachable; start it (or open the ssh tunnel), "
                f"or point LTXV_SERVER_URL / ltxv_server_url at it")
        status = health.get("status", "ready")
        if status != "ready":
            # Loading the weights and warming the shape takes minutes; the
            # warm list is empty until then, so say so instead of naming shapes.
            raise RendererNotReady(
                f"the LTX-2.5 server at {self.server_url} is still {status}; wait until /health says \"ready\"")
        rate = float(health.get("frame_rate", DEFAULT_FRAME_RATE))
        if rate != shape.fps:
            raise RendererNotReady(f"the server renders at {rate:g} fps; the shape was built for {shape.fps:g}")
        shape.check_warm(health.get("warm_frames"))
        return {"frames": shape.render_frames, "warm_frames": sorted(set(health["warm_frames"])),
                "audio_ceiling_lufs": health.get("audio_ceiling_lufs")}

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
        width, height = self.size
        data, notes = fit_reference(path, width, height)
        for note in notes:
            logger.warning("Reference image %s: %s", path, note)
        return base64.b64encode(data).decode()

    def check_reference(self, path: Path | str) -> dict:
        """Validate the reference against the server's render size before
        the run starts. Raises `ReferenceRefused` when it is not at the
        render aspect; warnings are returned (and logged), not raised. When
        the server cannot be probed yet the check is left to the first render."""
        if self._probe_health() is None:
            return {"checked": False, "reason": "server not reachable; checked at the first render"}
        width, height = self.size
        _, notes = fit_reference(path, width, height)
        for note in notes:
            logger.warning("Reference image %s: %s", path, note)
        return {"checked": True, "size": [width, height], "warnings": notes}

    # ── Rendering ────────────────────────────────────────────────────

    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
        shape: ClipShape,
        clip_id: int | None = None,
        parent_clip_id: int | None = None,
    ) -> str | None:
        try:
            return await asyncio.to_thread(
                self._synthesize_sync, image_path, prompt, turn, shape,
                clip_id=clip_id, parent_clip_id=parent_clip_id,
            )
        except Exception as exc:
            logger.exception("LTX-2.5 render failed on turn %s: %s", turn, exc)
            return None

    def _synthesize_sync(
        self,
        image_path: Path | str,
        prompt: str,
        turn: int,
        shape: ClipShape,
        *,
        clip_id: int | None = None,
        parent_clip_id: int | None = None,
    ) -> str | None:
        # Remember the original seed image so we can re-init after a
        # server restart. Always the reference, never a later frame.
        if self._seed_image_path is None:
            self._seed_image_path = str(image_path)

        payload: dict = {
            "prompt": prompt,
            "continuity_strength": self.continuity_strength,
            "continuity_frames": self.continuity_frames,
            "num_frames": shape.render_frames,
        }
        if self.seed_key:
            payload["seed"] = derive_seed(self.seed_key, turn)
        if not self._initialized:
            payload["reference_image"] = self._encode_reference(self._seed_image_path)
        if clip_id is not None:
            payload["clip_id"] = clip_id
        if parent_clip_id is not None:
            payload["parent_clip_id"] = parent_clip_id
        self.last_render = {"requested_frames": payload["num_frames"],
                            "reference": "reference_image" in payload,
                            "seed": payload.get("seed"),
                            "clip_id": clip_id, "parent_clip_id": parent_clip_id}

        url = f"{self.server_url}/generate"
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        # Server lost its session (a restart): send the reference again with
        # the last clip's tail and retry once; the reference alone only when
        # the tail cannot be read, which resets the picture.
        if resp.status_code == 400 and "reference_image" in resp.text:
            payload["reference_image"] = self._encode_reference(self._seed_image_path)
            window = self._tail_frames(self._last_clip, self.continuity_frames) if self._last_clip else []
            if window:
                logger.warning("LTX-2.5 server lost state — restoring the window from the last clip's tail")
                payload["window_frames"] = window
                self.last_render = {**self.last_render, "reference": False, "restored": len(window)}
            else:
                logger.warning("LTX-2.5 server lost state — re-seeding reference")
                self.last_render = {**self.last_render, "reference": True, "reseeded": True}
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        # Fenced render: the server does not hold the parent's tail.
        if resp.status_code == 409 and "unknown_parent" in resp.text:
            logger.warning("LTX-2.5 server does not hold parent %s — resending with window_frames", parent_clip_id)
            window = (self._tail_frames(self._last_clip, self.continuity_frames)
                      if self._last_clip else [])
            if window:
                payload["window_frames"] = window
                self.last_render = {**self.last_render, "restored": len(window)}
                resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
            else:
                self.last_render = {**self.last_render, "unknown_parent": True}
                return None

        self.last_render = {**self.last_render, "status": resp.status_code,
                            **render_headers(resp.headers)}
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
        self._last_clip = str(out_path)

        logger.info(
            "LTX-2.5 turn %d rendered in %ss + %ss encode (%ss clip, audio=%s) → %s",
            turn,
            resp.headers.get("X-Latency-S", "?"),
            resp.headers.get("X-Encode-S", "?"),
            resp.headers.get("X-Duration-S", "?"),
            resp.headers.get("X-Has-Audio", "?"),
            out_path,
        )
        return str(out_path)


def _tail_frames_png(path: str, count: int, size: tuple[int, int]) -> list[str]:
    """The last `count` frames of a clip as base64 PNGs at `size`."""
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-sseof", "-1.5", "-i", path, "-vf", f"scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    frames = raw and [raw[i:i + w * h * 3] for i in range(0, len(raw), w * h * 3)]
    out = []
    for frame in (frames or [])[-count:]:
        buf = io.BytesIO()
        Image.frombytes("RGB", (w, h), frame).save(buf, format="PNG")
        out.append(base64.b64encode(buf.getvalue()).decode())
    return out


def render_headers(headers) -> dict:
    """The server's per-clip headers present in `headers`, typed; a value
    that does not parse is kept as the raw string."""
    out: dict = {}
    for name, (key, cast) in RENDER_HEADERS.items():
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            out[key] = cast(raw)
        except ValueError:
            out[key] = raw
    return out


def derive_seed(key: str, segment: int) -> int:
    """The render seed of `segment` under `key` (the session id): a stable
    32-bit hash, the same in every process, different for every segment
    and every key. Never one fixed seed for every clip (repeated noise
    texture); never random (irreproducible)."""
    digest = hashlib.sha256(f"{key}:{segment}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


class ReferenceRefused(ValueError):
    """The reference image is not at the render aspect."""


def exact_aspect(src_w: int, src_h: int, width: int, height: int) -> bool:
    """The render aspect to the pixel: the reference's height is its width
    at the render aspect, within one pixel of rounding (1920x1080 or
    1672x941 for a 512x288 frame, not 1672x930)."""
    return abs(src_h - src_w * height / width) <= 1.0


def fit_reference(path: Path | str, width: int, height: int) -> tuple[bytes, list[str]]:
    """The reference resized whole to a `width`x`height` render frame, as
    PNG bytes, and what is wrong with it. It must be at the render aspect
    to the pixel (`exact_aspect`): a crop would cut the subject and padding
    would be painted as a picture frame, so any other aspect raises
    `ReferenceRefused`. A reference smaller than the frame is upscaled with
    a warning."""
    notes: list[str] = []
    with Image.open(path) as img:
        img.load()
        src_w, src_h = img.size
        if not exact_aspect(src_w, src_h, width, height):
            raise ReferenceRefused(
                f"{src_w}x{src_h} is not at the render aspect {width}x{height} (to the pixel): "
                f"supply the reference at that aspect, e.g. {round(1080 * width / height)}x1080")
        if src_w < width or src_h < height:
            notes.append(f"{src_w}x{src_h} is smaller than the render size {width}x{height}; upscaled")
        rgb = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img.convert("RGBA")).convert("RGB")
    buf = io.BytesIO()
    rgb.resize((width, height), Image.LANCZOS).save(buf, format="PNG")
    return buf.getvalue(), notes
