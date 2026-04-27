"""Minimal terminal — just an input prompt.

Story output goes to the Markdown transcript in `logs/`, not to the
terminal. The runner prints brief operational notices (boot hint, save
notice, quit) directly via `print`. This module exists only to read a
line from stdin without blocking the asyncio loop.
"""

from __future__ import annotations

import asyncio


class TerminalUI:
    async def prompt_for_input(self, default: str = "") -> str:
        """Read one line from stdin without blocking the event loop."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: input("› ").strip(),
            )
        except EOFError:
            return default
