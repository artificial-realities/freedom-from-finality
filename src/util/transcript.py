"""Markdown transcript of a film: what each clip showed and said, for
reading the film back.

Per clip: the viewer's input it answers, the action, the compiled video
prompt, the dialogue and the world. The models' raw outputs are next to
it in the film's folder (`plans.jsonl`, `then.jsonl`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine import Emitted

logger = logging.getLogger(__name__)


class Transcript:
    def __init__(self, path: Path | str, *, film_id: str, config_name: str, first_frame: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Story transcript — {config_name}\n\n"
            f"- Film: `{film_id}`\n"
            f"- First frame: {first_frame}\n"
            f"- Started: {datetime.now().isoformat(timespec='seconds')}\n\n"
            "---\n\n"
        )
        self._write(header, mode="w")

    def note(self, text: str) -> None:
        """A line about the film itself (a rollback, a failure), between clips."""
        self._write(f"> **Note:** {text}\n\n")

    def clip(self, emitted: "Emitted") -> None:
        heading = f'"{emitted.input}"' if emitted.input.strip() else "_(silent)_"
        out = [f"## Clip {emitted.clip} — {heading}\n\n", f"{emitted.action}\n\n"]
        if emitted.end_state:
            out.append(f"- Ends on: {emitted.end_state}\n\n")
        out.append("**Prompt**\n\n")
        out.append(f"{emitted.prompt}\n\n")
        if emitted.on_screen:
            out.append(f"- On screen: {', '.join(emitted.on_screen)}\n")
        out.append(f"- Camera: {emitted.camera}\n")
        out.append(f"- Motion: {emitted.motion}\n")
        out.append(f"- Duration: {emitted.duration:.2f}s\n\n")
        if emitted.dialogue:
            out.append("**Dialogue**\n\n")
            out.extend(f"> {line}\n" for line in emitted.dialogue)
            out.append("\n")
        else:
            out.append("**Dialogue** — _(silent)_\n\n")
        if emitted.notes:
            out.append(emitted.notes.rstrip("\n") + "\n")
        out.append("\n---\n\n")
        self._write("".join(out))

    def _write(self, text: str, *, mode: str = "a") -> None:
        try:
            with self.path.open(mode, encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            logger.exception("Failed to write the transcript %s: %s", self.path, exc)
