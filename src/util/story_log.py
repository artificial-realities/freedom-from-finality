"""Markdown story transcript: what each segment showed and said, for
reading the film back.

Per turn: the beat, the compiled shot, the dialogue and the world. The
models' raw outputs are in the session directory (`plans.jsonl`,
`showrunner.jsonl`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.models.responses import Beat, Shot

logger = logging.getLogger(__name__)


class StoryLogger:
    def __init__(
        self,
        *,
        session_id: str,
        config_name: str,
        story_title: str,
        log_dir: Path | str = "logs",
    ) -> None:
        self.session_id = session_id
        self.config_name = config_name
        self.story_title = story_title

        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / f"{session_id}.md"

        self._write_header()

    def _write_header(self) -> None:
        header = (
            f"# Story transcript — {self.config_name}\n\n"
            f"- Session: `{self.session_id}`\n"
            f"- Story: {self.story_title or '(untitled)'}\n"
            f"- Started: {datetime.now().isoformat(timespec='seconds')}\n\n"
            "---\n\n"
        )
        try:
            self.log_file.write_text(header, encoding="utf-8")
        except OSError as exc:
            logger.exception("Failed to initialize transcript %s: %s", self.log_file, exc)

    def log_note(self, text: str) -> None:
        """A line about the run itself (a rollback, a failure), between turns."""
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(f"> **Note:** {text}\n\n")
        except OSError as exc:
            logger.exception("Failed to append to transcript %s: %s", self.log_file, exc)

    def log_turn(
        self,
        *,
        turn: int,
        user_input: str,
        beat: Beat | None,
        shot: Shot | None,
        dialogue: list[str],
        world_state: dict,
        context_brief: str,
        extra: str = "",
    ) -> None:
        section = self._format_turn(
            turn=turn,
            user_input=user_input,
            beat=beat,
            shot=shot,
            dialogue=dialogue,
            world_state=world_state,
            context_brief=context_brief,
            extra=extra,
        )
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(section)
        except OSError as exc:
            logger.exception("Failed to append to transcript %s: %s", self.log_file, exc)

    @staticmethod
    def _format_turn(
        *,
        turn: int,
        user_input: str,
        beat: Beat | None,
        shot: Shot | None,
        dialogue: list[str],
        world_state: dict,
        context_brief: str,
        extra: str = "",
    ) -> str:
        heading_input = f'"{user_input}"' if user_input.strip() else "_(silent)_"
        out = [f"## Turn {turn} — {heading_input}\n\n"]

        if beat is None:
            out.append("**Beat** — _no beat (parse failure)_\n\n")
        else:
            out.append("**Beat**\n\n")
            out.append(f"{beat.narration}\n\n")
            if beat.outcome and not (shot is not None and beat.outcome == shot.end_frame_description):
                out.append(f"- Outcome: {beat.outcome}\n")
            if beat.intent:
                out.append(f"- Beat: {beat.intent}\n")
            out.append("\n")

        if shot is None:
            out.append("**Shot** — _no shot (parse failure)_\n\n")
        else:
            out.append("**Shot**\n\n")
            out.append(f"{shot.i2v_prompt}\n\n")
            if shot.on_screen:
                out.append(f"- On screen: {', '.join(shot.on_screen)}\n")
            out.append(f"- Camera: {shot.camera}\n")
            out.append(f"- Motion: {shot.motion}\n")
            out.append(f"- End frame: {shot.end_frame_description}\n")
            out.append(f"- Duration: {shot.duration_seconds:.2f}s\n\n")

        if not dialogue:
            out.append("**Dialogue** — _(silent)_\n\n")
        else:
            out.append("**Dialogue**\n\n")
            for line in dialogue:
                out.append(f"> {line}\n")
            out.append("\n")

        out.append("**Memory**\n\n")
        location = world_state.get("camera_location")
        characters = world_state.get("characters")
        if location:
            out.append(f"- Location: {location}\n")
        if characters:
            out.append(f"- Characters: {characters}\n")
        if context_brief:
            out.append(f"- Context brief (next turn): {context_brief}\n")
        if extra:
            out.append("\n" + extra.rstrip("\n") + "\n")
        out.append("\n---\n\n")

        return "".join(out)
