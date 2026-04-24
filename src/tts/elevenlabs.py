"""ElevenLabs TTS — fail-soft voiceover for Attenborough.

Only invoked when `audio_enabled: true`. If anything goes wrong, log
and continue: the text is already captured, and TTS must never break
the turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ElevenLabsTTS:
    def __init__(
        self,
        voice_id: str,
        api_key: str | None = None,
        audio_dir: Path | str = "logs/audio",
        model_id: str = "eleven_multilingual_v2",
    ) -> None:
        self.voice_id = voice_id
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self._client = None  # lazy — import only when actually used

    def _get_client(self):
        if self._client is None:
            from elevenlabs.client import ElevenLabs  # noqa: WPS433
            self._client = ElevenLabs(api_key=self.api_key)
        return self._client

    async def synthesize(self, text: str, turn: int) -> str | None:
        """Render `text` to an MP3; return the file path or None on failure."""
        if not text or not text.strip():
            return None
        if not self.voice_id or not self.api_key:
            logger.warning("ElevenLabs not configured — voice_id/api_key missing")
            return None

        try:
            return await asyncio.to_thread(self._synthesize_sync, text, turn)
        except Exception as exc:
            logger.exception("ElevenLabs TTS failed on turn %s: %s", turn, exc)
            return None

    def _synthesize_sync(self, text: str, turn: int) -> str:
        client = self._get_client()
        audio = client.text_to_speech.convert(
            voice_id=self.voice_id,
            model_id=self.model_id,
            text=text,
            output_format="mp3_44100_128",
        )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.audio_dir / f"turn_{turn:04d}_{stamp}.mp3"
        with path.open("wb") as f:
            for chunk in audio:
                if chunk:
                    f.write(chunk)
        return str(path)
