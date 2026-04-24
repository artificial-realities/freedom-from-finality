"""Minimal terminal view of the story.

The point is to validate the pipeline, not to be pretty. Renders each
turn's beat and commentary; prompts for user input between turns.
Ctrl+C quits.
"""

from __future__ import annotations

import asyncio
import logging

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.state.story_state import StoryState

logger = logging.getLogger(__name__)


class TerminalUI:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render_opening(self, state: StoryState) -> None:
        protagonist = state.characters[0] if state.characters else None
        lines = [
            Text(state.title or "(untitled)", style="bold cyan"),
            Text(""),
            Text(state.synopsis or "", style="italic"),
        ]
        if protagonist:
            lines.append(Text(""))
            lines.append(Text(f"Protagonist: {protagonist.name}", style="bold"))
            lines.append(Text(protagonist.description))
        self.console.print(Panel.fit(Text.assemble(*[(l.plain + "\n", l.style or "") for l in lines]), title="ClankerStudios"))

    def render_turn(self, state: StoryState) -> None:
        turn = state.turn_number
        beat = state.current_beat
        commentary = state.current_commentary

        if beat is None:
            self.console.print(f"[dim]Turn {turn}: no beat produced (parse failure).[/dim]")
        else:
            body = Text()
            body.append(beat.narration + "\n\n", style="")
            body.append(f"action: {beat.action}\n", style="dim")
            body.append(f"outcome: {beat.outcome}", style="dim")
            self.console.print(Panel(body, title=f"Turn {turn} — Beat", border_style="green"))

        if commentary is not None and commentary.voiceover:
            self.console.print(
                Panel(Text(commentary.voiceover, style="italic"),
                      title="Voice-over", border_style="magenta")
            )

    def render_error(self, message: str) -> None:
        self.console.print(f"[red]{message}[/red]")

    async def prompt_for_input(self, default: str = "") -> str:
        """Prompt asynchronously without blocking the event loop.

        Returns the user's line of input, or the default if they press Enter.
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: input("› ").strip(),
            )
        except EOFError:
            return default
