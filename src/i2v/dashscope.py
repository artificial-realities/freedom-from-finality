"""Alibaba DashScope i2v backend (Wan2.x family).

Ported from notebooks/wan_test.ipynb and notebooks/i2v_chaining_test.ipynb.
The DashScope API is async on Alibaba's side: submit → task_id → poll
until SUCCEEDED → download an mp4 from a URL that expires in 24h.

We wrap the blocking SDK calls in `asyncio.to_thread` so the agent
graph stays async-friendly. Render failures (network, timeout, API
error) return None — i2v never breaks the turn.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

import requests

from src.i2v.base import I2VBackend

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://dashscope-intl.aliyuncs.com/api/v1"
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 300  # 5 min hard cap per render


class DashScopeI2V(I2VBackend):
    def __init__(
        self,
        model: str = "wan2.2-i2v-flash",
        resolution: str = "480P",
        duration: int = 5,
        output_dir: Path | str = "logs/video",
        api_key: str | None = None,
        endpoint_url: str | None = None,
        prompt_extend: bool = False,
        watermark: bool = False,
    ) -> None:
        self.model = model
        self.resolution = resolution
        self.duration = duration
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.endpoint_url = endpoint_url or os.getenv("DASHSCOPE_ENDPOINT_URL", DEFAULT_ENDPOINT)
        self.prompt_extend = prompt_extend
        self.watermark = watermark
        self._configured = False

    def _configure_sdk(self) -> bool:
        """Lazy SDK import + endpoint configuration."""
        if self._configured:
            return True
        if not self.api_key:
            logger.warning("DashScope not configured — DASHSCOPE_API_KEY missing")
            return False
        try:
            import dashscope  # noqa: WPS433
            dashscope.base_http_api_url = self.endpoint_url
        except ImportError:
            logger.exception("dashscope SDK not installed — `pip install dashscope`")
            return False
        self._configured = True
        return True

    async def synthesize(
        self,
        *,
        image_path: Path | str,
        prompt: str,
        turn: int,
    ) -> str | None:
        if not self._configure_sdk():
            return None
        try:
            return await asyncio.to_thread(self._synthesize_sync, image_path, prompt, turn)
        except Exception as exc:
            logger.exception("DashScope render failed on turn %s: %s", turn, exc)
            return None

    def _synthesize_sync(self, image_path: Path | str, prompt: str, turn: int) -> str | None:
        from dashscope import VideoSynthesis  # noqa: WPS433

        img_url = _encode_image_to_data_url(image_path)
        rsp = VideoSynthesis.async_call(
            api_key=self.api_key,
            model=self.model,
            img_url=img_url,
            prompt=prompt,
            resolution=self.resolution,
            duration=self.duration,
            prompt_extend=self.prompt_extend,
            watermark=self.watermark,
        )
        if rsp.status_code != HTTPStatus.OK:
            logger.warning("DashScope submit failed (turn %s): %s — %s",
                           turn, rsp.code, rsp.message)
            return None

        task_id = rsp.output.task_id
        result = self._wait_for_task(task_id, turn)
        if result is None:
            return None

        video_url = result.output.video_url
        path = self._download_video(video_url, turn)
        return str(path) if path else None

    def _wait_for_task(self, task_id: str, turn: int):
        from dashscope import VideoSynthesis  # noqa: WPS433

        deadline = time.monotonic() + POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            status = VideoSynthesis.fetch(task_id, api_key=self.api_key)
            if status.status_code != HTTPStatus.OK:
                logger.warning("DashScope poll failed (turn %s, task %s): %s — %s",
                               turn, task_id, status.code, status.message)
                return None
            task_status = status.output.task_status
            if task_status == "SUCCEEDED":
                return status
            if task_status in ("FAILED", "CANCELED", "UNKNOWN"):
                logger.warning("DashScope task %s ended in state=%s code=%s message=%s",
                               task_id, task_status,
                               status.output.get("code", "N/A"),
                               status.output.get("message", "N/A"))
                return None
            time.sleep(POLL_INTERVAL_S)
        logger.warning("DashScope task %s timed out after %ss", task_id, POLL_TIMEOUT_S)
        return None

    def _download_video(self, url: str, turn: int) -> Path | None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"turn_{turn:04d}_{stamp}.mp4"
        try:
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return path
        except Exception as exc:
            logger.exception("DashScope download failed (turn %s): %s", turn, exc)
            return None


def _encode_image_to_data_url(image_path: Path | str) -> str:
    """Read a local image and encode it as a base64 data URL for the API."""
    p = Path(image_path)
    with p.open("rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".bmp": "image/bmp", ".webp": "image/webp"}.get(p.suffix.lower(), "image/png")
    return f"data:{mime};base64,{b64}"
