"""Session-scoped logger for every LLM call.

Each session writes one JSON file to `logs/`. The file is rewritten
after every call — safe against mid-run crashes and simple to consume
post-hoc. Adapted from reference/interaction_logger.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class InteractionLogger:
    def __init__(
        self,
        session_label: str,
        config_name: str,
        scenario: str = "",
        story_title: str = "",
        log_dir: Path | str = "logs",
    ) -> None:
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        safe_label = "".join(
            c if c.isalnum() or c in (" ", "-", "_") else "_"
            for c in session_label
        ).strip().replace(" ", "_")[:50] or "session"

        self.log_file = self.log_dir / f"{safe_label}_{self.session_id}.json"
        self.config_name = config_name
        self.scenario = scenario
        self.story_title = story_title

        self._data: dict[str, Any] = {
            "session_id": self.session_id,
            "config": config_name,
            "scenario": scenario,
            "story": story_title,
            "start_time": datetime.now().isoformat(),
            "interactions": [],
        }
        self._flush()

    def log_llm_call(
        self,
        agent: str,
        turn: int,
        model: str,
        system_prompt: str,
        user_prompt: str,
        raw_response: str,
        parsed_response: Any,
        token_usage: dict[str, int] | None = None,
        latency_ms: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        interaction: dict[str, Any] = {
            "agent": agent,
            "turn": turn,
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "parameters": {
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            "prompt": {"system": system_prompt, "user": user_prompt},
            "response": {"raw": raw_response, "parsed": parsed_response},
            "token_usage": token_usage or {},
            "latency_ms": latency_ms,
        }
        if extra:
            interaction.update(extra)

        self._data["interactions"].append(interaction)
        self._flush()

    def log_tts(
        self,
        turn: int,
        voice_id: str,
        text: str,
        audio_path: str | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        self._data["interactions"].append({
            "agent": "elevenlabs",
            "turn": turn,
            "timestamp": datetime.now().isoformat(),
            "voice_id": voice_id,
            "text": text,
            "audio_path": audio_path,
            "success": success,
            "error": error,
        })
        self._flush()

    def log_event(self, event: str, turn: int, payload: dict[str, Any]) -> None:
        self._data["interactions"].append({
            "event": event,
            "turn": turn,
            "timestamp": datetime.now().isoformat(),
            **payload,
        })
        self._flush()

    def _flush(self) -> None:
        try:
            with self.log_file.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, default=str)
        except OSError as exc:
            logger.exception("Failed to write log file %s: %s", self.log_file, exc)
