"""Markdown story transcript — agent outputs only, for narrative review.

Sits next to InteractionLogger in `logs/` with a matching basename so the
two files are obviously paired. Contains per-turn agent outputs (Beat,
Shot, Commentary, Memory) and nothing else — no prompts, no token counts,
no parameters. The JSON `interaction_logger` remains the source of truth
for everything else; this file is for skimming the story flow.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.models.responses import Beat, Commentary, Shot
from src.util.interaction_logger import InteractionLogger

logger = logging.getLogger(__name__)


class StoryLogger:
    def __init__(self, interaction_logger: InteractionLogger) -> None:
        # Share session_id + base path with the JSON log so the two files
        # pair up by name in `logs/`.
        json_path = interaction_logger.log_file
        self.log_file = json_path.with_suffix(".md")
        self.config_name = interaction_logger.config_name
        self.scenario = interaction_logger.scenario
        self.story_title = interaction_logger.story_title
        self.session_id = interaction_logger.session_id

        self._write_header()

    def _write_header(self) -> None:
        scenario = self.scenario or "interactive"
        header = (
            f"# Story transcript — {self.config_name} / {scenario}\n\n"
            f"- Session: `{self.session_id}`\n"
            f"- Story: {self.story_title or '(untitled)'}\n"
            f"- Started: {datetime.now().isoformat(timespec='seconds')}\n\n"
            "---\n\n"
        )
        try:
            self.log_file.write_text(header, encoding="utf-8")
        except OSError as exc:
            logger.exception("Failed to initialize transcript %s: %s", self.log_file, exc)

    def log_turn(
        self,
        *,
        turn: int,
        user_input: str,
        beat: Beat | None,
        shot: Shot | None,
        commentary: Commentary | None,
        world_state: dict,
        narrative_memory: str,
        context_brief: str,
    ) -> None:
        section = self._format_turn(
            turn=turn,
            user_input=user_input,
            beat=beat,
            shot=shot,
            commentary=commentary,
            world_state=world_state,
            narrative_memory=narrative_memory,
            context_brief=context_brief,
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
        commentary: Commentary | None,
        world_state: dict,
        narrative_memory: str,
        context_brief: str,
    ) -> str:
        heading_input = f'"{user_input}"' if user_input.strip() else "_(silent)_"
        out = [f"## Turn {turn} — {heading_input}\n\n"]

        if beat is None:
            out.append("**Beat** — _no beat (parse failure)_\n\n")
        else:
            out.append("**Beat**\n\n")
            out.append(f"{beat.narration}\n\n")
            out.append(f"- Action: {beat.action}\n")
            out.append(f"- Outcome: {beat.outcome}\n")
            if beat.short_term_narrative:
                out.append(f"- Short-term direction: {beat.short_term_narrative}\n")
            if beat.long_term_narrative:
                out.append(f"- Long-term direction: {beat.long_term_narrative}\n")
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
            out.append(f"- Duration: {shot.duration_seconds}s\n\n")

        if commentary is None:
            out.append("**Voice-over** — _no commentary (parse failure)_\n\n")
        elif not commentary.voiceover.strip():
            out.append("**Voice-over** — _(silent)_\n\n")
        else:
            out.append("**Voice-over**\n\n")
            for line in commentary.voiceover.splitlines() or [commentary.voiceover]:
                out.append(f"> {line}\n")
            out.append("\n")

        out.append("**Memory**\n\n")
        inventory = world_state.get("inventory")
        location = world_state.get("protagonist_location")
        characters = world_state.get("characters")
        if inventory is not None:
            out.append(f"- Inventory: {inventory if inventory else '_(empty)_'}\n")
        if location:
            out.append(f"- Location: {location}\n")
        if characters:
            out.append(f"- Characters: {characters}\n")
        if narrative_memory:
            out.append(f"- Narrative memory: {narrative_memory}\n")
        if context_brief:
            out.append(f"- Context brief (next turn): {context_brief}\n")
        out.append("\n---\n\n")

        return "".join(out)
