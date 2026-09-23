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
import hashlib
import io
import logging
import statistics
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

from src.i2v.base import I2VBackend
from src.i2v.shape import ClipShape, ShapeRegistry, UnregisteredShape

logger = logging.getLogger(__name__)

# Generous timeout — the first call can include model download + warmup.
REQUEST_TIMEOUT_S = 600
# Short timeout for the one-off capability probe.
PROBE_TIMEOUT_S = 10

# Fallbacks when the server can't be probed: LTX-2.5 renders at 24fps and
# the server defaults to 960x544 (the model card's recommended size).
DEFAULT_FRAME_RATE = 24.0
DEFAULT_SIZE = (960, 544)
# Fraction of the frame height the reference subject is scaled to when
# letterboxed: a wide product shot with margins, not a crop.
REFERENCE_SCALE = 0.8
# The server's per-clip response headers, as `last_render` keys for the
# run's timing log.
RENDER_HEADERS: dict[str, tuple[str, type]] = {
    "X-Latency-S": ("latency_s", float),
    "X-Encode-S": ("encode_s", float),
    "X-Timings": ("timings", str),
    "X-Num-Frames": ("num_frames", int),
    "X-Duration-S": ("duration_s", float),
    "X-Frame-Rate": ("frame_rate", float),
    "X-Has-Audio": ("has_audio", str),
    "X-Variant": ("variant", str),
    "X-Encoder": ("encoder", str),
    # AUD-03's candidate (the server's LTX2_AUDIO_CEILING_LUFS; experiment only)
    "X-Audio-LUFS": ("audio_lufs", float),
    "X-Audio-TP": ("audio_tp", float),
    "X-Audio-Gain-dB": ("audio_gain_db", float),
}


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
        seed_key: str = "",
        anchor_strength: float = 0.0,
        anchor_index: int = -1,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.continuity_strength = continuity_strength
        self.continuity_frames = continuity_frames
        # Identity anchor: the server also conditions on the reference image
        # as a guiding keyframe at `anchor_index` (latent frames, -1 = last)
        # with this strength, so the clip has to return to the sheet. 0 = off.
        self.anchor_strength = anchor_strength
        self.anchor_index = anchor_index
        # How the reference is fitted to the render frame (`fit_reference`):
        # within 5 % of the render aspect it is cover-resized; outside it
        # "pad" letterboxes onto a `reference_fill` canvas with a warning (a
        # square reference sent to a 3:2 render otherwise loses the top of
        # the subject and the model invents a new head), "strict" refuses,
        # "none" sends the file as-is.
        if reference_fit not in REFERENCE_FITS:
            raise ValueError(f"reference_fit must be one of {REFERENCE_FITS}, not {reference_fit!r}")
        self.reference_fit = reference_fit
        self.reference_fill = reference_fill
        self.negative_prompt = negative_prompt  # "" → the server's default; only the full variant encodes it
        # Every clip's seed is derived from this key and its segment
        # (`derive_seed`), so a rerun under the same key reproduces the
        # noise and experiment arms share it. "" → the server draws one.
        self.seed_key = seed_key
        self.model = "ltx-2.5"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._seed_image_path: str | None = None
        self._initialized = False
        # Probed from the server on first use so client and server can't
        # silently disagree about the frame rate or the render size.
        self._frame_rate: float | None = None
        self._size: tuple[int, int] | None = None

    @property
    def last_latency_s(self) -> float | None:
        """Server-reported render time of the last clip (X-Latency-S);
        None until a clip has rendered."""
        value = self.last_render.get("latency_s")
        return value if isinstance(value, float) else None

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

    def check_shapes(self, registry: ShapeRegistry) -> dict:
        """RUN-08: the server must report every registered shape among the
        frame counts it warmed (`/health` `warm_frames`), at the frame rate
        the shapes were built for; otherwise the run is refused before it
        starts, not stalled for minutes mid-film by a compile."""
        health = self._probe_health()
        if health is None:
            raise UnregisteredShape(f"the LTX-2.5 server at {self.server_url} is not reachable; cannot check its warm shapes")
        rate = float(health.get("frame_rate", DEFAULT_FRAME_RATE))
        if rate != registry.default.fps:
            raise UnregisteredShape(f"the server renders at {rate:g} fps; the shapes were built for {registry.default.fps:g}")
        registry.check_warm(health.get("warm_frames"))
        return {"frames": registry.frames, "warm_frames": sorted(set(health["warm_frames"]))}

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
        if self.reference_fit == "none":
            return _encode_image(path)
        width, height = self.size
        data, notes = fit_reference(path, width, height, fit=self.reference_fit, fill=self.reference_fill)
        for note in notes:
            logger.warning("Reference image %s: %s", path, note)
        return base64.b64encode(data).decode()

    def check_reference(self, path: Path | str) -> dict:
        """Validate the reference against the server's render size before
        the run starts: how it will be fitted and why. Raises
        `ReferenceRefused` under `reference_fit: strict` when the aspect is
        off; warnings are returned (and logged), not raised. When the
        server cannot be probed yet the check is left to the first render."""
        if self._probe_health() is None:
            return {"checked": False, "reason": "server not reachable; checked at the first render"}
        width, height = self.size
        if self.reference_fit == "none":
            return {"checked": True, "fit": "none", "size": [width, height], "warnings": []}
        _, notes = fit_reference(path, width, height, fit=self.reference_fit, fill=self.reference_fill)
        for note in notes:
            logger.warning("Reference image %s: %s", path, note)
        mode = "cover" if within_aspect(path, width, height) else "pad"
        return {"checked": True, "fit": mode, "size": [width, height], "warnings": notes}

    # ── Rendering ────────────────────────────────────────────────────

    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
        shape: ClipShape,
    ) -> str | None:
        try:
            return await asyncio.to_thread(
                self._synthesize_sync, image_path, prompt, turn, shape,
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
        if self.negative_prompt:
            payload["negative_prompt"] = self.negative_prompt
        if self.seed_key:
            payload["seed"] = derive_seed(self.seed_key, turn)
        if self.anchor_strength > 0.0:
            payload["anchor_strength"] = self.anchor_strength
            payload["anchor_index"] = self.anchor_index
        if not self._initialized:
            payload["reference_image"] = self._encode_reference(self._seed_image_path)
        self.last_render = {"requested_frames": payload["num_frames"],
                            "reference": "reference_image" in payload,
                            "seed": payload.get("seed")}

        url = f"{self.server_url}/generate"
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

        # Server lost its session (restart / reset) — re-seed and retry once.
        if resp.status_code == 400 and "reference_image" in resp.text:
            logger.warning("LTX-2.5 server lost state — re-seeding reference")
            payload["reference_image"] = self._encode_reference(self._seed_image_path)
            self.last_render = {**self.last_render, "reference": True, "reseeded": True}
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)

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

        logger.info(
            "LTX-2.5 turn %d rendered in %ss + %ss encode (%ss clip, audio=%s) → %s",
            turn,
            resp.headers.get("X-Latency-S", "?"),
            resp.headers.get("X-Encode-S", "?"),
            resp.headers.get("X-Duration-S", "?"),
            resp.headers.get("X-Has-Audio", "?"),
            out_path,
        )
        if resp.headers.get("X-Timings"):
            logger.info("LTX-2.5 turn %d timings: %s", turn, resp.headers["X-Timings"])
        return str(out_path)


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


REFERENCE_FITS = ("pad", "strict", "none")
# A reference within this fraction of the render aspect is cover-resized
# (scaled to fill the frame, the overhang cropped evenly); outside it the
# `reference_fit` policy applies.
ASPECT_TOLERANCE = 0.05
# Standard deviation of the border pixels (0-255, mean over the channels)
# above which a letterbox fill cannot continue the image's own background:
# the padding would show as a frame around a picture.
BORDER_SD_LIMIT = 12.0


class ReferenceRefused(ValueError):
    """`reference_fit: strict` and the reference is not at the render aspect."""


def within_aspect(path: Path | str, width: int, height: int) -> bool:
    with Image.open(path) as img:
        ratio = (img.width / img.height) / (width / height)
    return abs(ratio - 1.0) <= ASPECT_TOLERANCE


def border_sd(img: Image.Image, band: int = 4) -> float:
    """Spread of the outer `band` pixels (the standard deviation per
    channel, averaged): 0 for a uniform background."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    samples = [px[x, y] for y in range(h) for x in range(w)
               if x < band or y < band or x >= w - band or y >= h - band]
    return statistics.fmean(statistics.pstdev(c) for c in zip(*samples))


def fit_reference(
    path: Path | str, width: int, height: int, *, fit: str = "pad", fill: str = "auto",
) -> tuple[bytes, list[str]]:
    """The reference fitted to a `width`x`height` render frame, as PNG
    bytes, and what is wrong with it. Within `ASPECT_TOLERANCE` of the
    render aspect it is cover-resized (an authored master at the render
    aspect, the intended case); outside it `fit="pad"` letterboxes it with
    a warning (and a second warning when the border is too uneven for the
    fill to continue it), `fit="strict"` raises `ReferenceRefused`. A
    reference smaller than the render frame is upscaled with a warning."""
    notes: list[str] = []
    with Image.open(path) as img:
        img.load()
        src_w, src_h = img.size
        ratio = (src_w / src_h) / (width / height)
        if abs(ratio - 1.0) <= ASPECT_TOLERANCE:
            if src_w < width or src_h < height:
                notes.append(f"{src_w}x{src_h} is smaller than the render size {width}x{height}; upscaled")
            rgb = Image.alpha_composite(
                Image.new("RGBA", img.size, "white"), img.convert("RGBA")).convert("RGB")
            scale = max(width / src_w, height / src_h)
            size = (max(width, round(src_w * scale)), max(height, round(src_h * scale)))
            rgb = rgb.resize(size, Image.LANCZOS)
            left, top = (size[0] - width) // 2, (size[1] - height) // 2
            out = rgb.crop((left, top, left + width, top + height))
            buf = io.BytesIO()
            out.save(buf, format="PNG")
            return buf.getvalue(), notes
        off = f"aspect {src_w}x{src_h} is {abs(ratio - 1.0):.0%} off the render aspect {width}x{height}"
        if fit == "strict":
            raise ReferenceRefused(f"{off}; reference_fit is strict (supply a master at the render aspect)")
        notes.append(f"{off}; letterboxed (supply a master at the render aspect to avoid it)")
        sd = border_sd(img)
        if sd > BORDER_SD_LIMIT:
            notes.append(f"its border is not uniform (sd {sd:.0f}); the letterbox fill will show as a frame")
        scaled_h = height * REFERENCE_SCALE
        if src_h < scaled_h and src_w < width * REFERENCE_SCALE:
            notes.append(f"{src_w}x{src_h} is smaller than the subject area of a {width}x{height} frame; upscaled")
    return letterbox_reference(path, width, height, fill=fill), notes


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
