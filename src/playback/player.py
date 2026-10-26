"""The player boundary of the live loop.

The producer turns each clip into a `Playable`; a `Player` shows it and
collects what the viewer types while it plays. The server is the clock:
`play` returns when the item's time on air is up, so the producer paces
itself against real playback whether anyone is watching or not. The web
player (`src.web.WebPlayer`) is the one surface.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Callable

from pydantic import BaseModel, Field


class Playable(BaseModel):
    """One item of the film as the viewer sees it: a clip, or without a
    video backend just its text."""

    clip: int
    path: str | None = None          # mp4 to play; None = a text-only clip (no video backend)
    duration: float = 6.0            # seconds on air (the hold)
    # The seconds at this clip's head that repeat the previous clip's tail,
    # over which the page cuts the picture and crossfades the sound (0 for
    # a clip seeded from the reference), and whether the clip carries speech
    # (its sound then comes in at once).
    overlap_seconds: float = 0.0
    speech: bool = False
    action: str = ""                 # what the camera sees: the text a text-only film shows
    dialogue: list[str] = Field(default_factory=list)   # the clip's spoken lines, "Name: line"


class Player(ABC):
    """What the live loop needs from a viewer-facing surface."""

    def __init__(self) -> None:
        self._inputs: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._arrived = asyncio.Event()   # set while an input is waiting
        self._input_no = 0
        # For the timing log: every input gets an id as it arrives;
        # `taken` holds the ids of the inputs the last `next_input` returned.
        self.taken: list[int] = []
        # Set by the live loop: a non-blank input arrived (id, text).
        self.on_input: Callable[[int, str], None] | None = None

    async def start(self) -> None:
        """Bring the surface up before the first clip."""

    async def stop(self) -> None:
        """Tear the surface down after the last clip."""

    def prepare(self, item: Playable) -> None:
        """A finished item is queued behind the one on air (prefetch hint)."""

    @abstractmethod
    async def play(self, item: Playable) -> None:
        """Show `item` and return when its time on air is up."""

    async def next_input(self) -> str:
        """Everything the viewer typed since the last call, oldest first,
        joined into one input. Empty when nothing arrived."""
        texts: list[str] = []
        ids: list[int] = []
        while not self._inputs.empty():
            input_id, text = await self._inputs.get()
            text = text.strip()
            if text:
                texts.append(text)
                ids.append(input_id)
        self._arrived.clear()
        self.taken = ids
        return " ".join(texts)

    def has_input(self) -> bool:
        return not self._inputs.empty()

    async def wait_input(self, timeout: float | None) -> bool:
        """Wait until the viewer has typed something or `timeout` seconds
        pass. True when an input is waiting."""
        if self.has_input():
            return True
        try:
            await asyncio.wait_for(self._arrived.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def push_input(self, text: str) -> None:
        self._input_no += 1
        self._inputs.put_nowait((self._input_no, text))
        self._arrived.set()
        if self.on_input is not None and text.strip():
            self.on_input(self._input_no, text.strip())

    async def _hold(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))
