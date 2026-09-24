"""The player boundary of the live loop.

The producer turns each segment into a `Playable`; a `Player` shows it and
collects what the viewer types while it plays. The server is the clock:
`play` returns when the item's time on air is up, so the producer paces
itself against real playback whether the viewer is an ffplay window, a
browser, or nobody at all.

Two players exist: `TerminalPlayer` (an ffplay window and a stdin reader,
the original live demo) and `src.web.WebPlayer` (a browser page that
streams the film and takes input mid-stream). The loop does not know
which one it has.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Playable(BaseModel):
    """One item of the film as the viewer sees it: a clip (or, headless,
    just its text) plus the views of the segment that produced it."""

    turn: int
    path: str | None = None          # mp4 to play; None = text-only segment (no video backend)
    duration: float = 5.0            # seconds on air (the hold)
    # The overlap seam (AUD-02 / LTX-12, D-03; the config's and the page's
    # `seam: "overlap"`): the seconds at this clip's head that repeat the
    # previous clip's tail, over which the page cuts the picture and
    # crossfades the sound (0 for a clip seeded from the reference), and
    # whether the clip carries speech (its sound comes in at once).
    overlap_seconds: float = 0.0
    speech: bool = False
    user_input: str = ""
    narration: str = ""
    voiceover: str = ""
    on_screen: list[str] = Field(default_factory=list)
    camera: str = ""
    intervention: str = "none"       # how the Director handled `user_input`
    intervention_reason: str = ""
    scene: str = ""                  # "scene N: goal"
    beat: str = ""                   # the beat this clip belongs to, in a few words
    clip: str = ""                   # "2/3": the clip's place in its beat


class Player(ABC):
    """What the live loop needs from a viewer-facing surface."""

    def __init__(self, *, time_scale: float = 1.0) -> None:
        # >1 shortens every wait; only tests use it.
        self.time_scale = max(time_scale, 1e-6)
        self._inputs: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._arrived = asyncio.Event()   # set while a line is waiting
        self._line_no = 0
        # For the timing log: every line gets an id as it arrives;
        # `taken` holds the ids of the lines the last `next_input` returned.
        self.taken: list[int] = []
        # Observers the live loop sets for the timing log: a non-blank line
        # arrived (id, text), and a browser beacon arrived (web player only).
        self.on_line: Callable[[int, str], None] | None = None
        self.on_beacon: Callable[[dict], None] | None = None

    async def start(self, *, title: str, session_id: str) -> None:
        """Bring the surface up before the first segment."""

    async def stop(self) -> None:
        """Tear the surface down after the last segment."""

    def prepare(self, item: Playable) -> None:
        """A finished item is queued behind the one on air (prefetch hint)."""

    def status(self, text: str) -> None:
        """A one-line note about what the producer is doing right now."""

    @abstractmethod
    async def play(self, item: Playable) -> None:
        """Show `item` and return when its time on air is up."""

    async def next_input(self) -> str:
        """Everything the viewer typed since the last call, oldest first,
        joined into one line. Empty when nothing arrived."""
        lines: list[str] = []
        ids: list[int] = []
        while not self._inputs.empty():
            line_id, line = await self._inputs.get()
            line = line.strip()
            if line:
                lines.append(line)
                ids.append(line_id)
        self._arrived.clear()
        self.taken = ids
        return " ".join(lines)

    def has_input(self) -> bool:
        return not self._inputs.empty()

    async def wait_input(self, timeout: float | None) -> bool:
        """Wait until the viewer has typed something or `timeout` seconds
        pass. True when a line is waiting."""
        if self.has_input():
            return True
        try:
            await asyncio.wait_for(self._arrived.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def push_input(self, text: str) -> None:
        self._line_no += 1
        self._inputs.put_nowait((self._line_no, text))
        self._arrived.set()
        if self.on_line is not None and text.strip():
            self.on_line(self._line_no, text.strip())

    async def _hold(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds) / self.time_scale)


class TerminalPlayer(Player):
    """The original live demo: an ffplay window that holds its last frame
    between clips, and a stdin thread that reads viewer lines."""

    def __init__(self, *, time_scale: float = 1.0) -> None:
        super().__init__(time_scale=time_scale)
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, *, title: str, session_id: str) -> None:
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._stdin_loop, daemon=True).start()

    def _stdin_loop(self) -> None:
        """Re-emits a `› ` prompt after every Enter so the terminal feels
        like a chat input field: the previous line stays as scrollback, a
        fresh prompt waits below."""
        sys.stdout.write("› ")
        sys.stdout.flush()
        while not self._stop.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:  # EOF
                return
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self.push_input, line.strip())
            sys.stdout.write("› ")
            sys.stdout.flush()

    async def play(self, item: Playable) -> None:
        if item.path:
            # Launch the new window before killing the old one: no flicker.
            new_proc = await _launch_persistent_ffplay(item.path)
            await asyncio.sleep(0.5)
            await self._kill()
            self._proc = new_proc
        else:
            sys.stdout.write(f"\n[segment {item.turn}] {item.narration}\n")
            if item.voiceover:
                sys.stdout.write(f"  > {item.voiceover}\n")
            sys.stdout.write("› ")
            sys.stdout.flush()
        await self._hold(item.duration)

    async def stop(self) -> None:
        self._stop.set()
        await self._kill()

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass


async def _launch_persistent_ffplay(clip_path: str) -> asyncio.subprocess.Process | None:
    """Launch ffplay *without* ``-autoexit`` so it holds the last frame."""
    if not shutil.which("ffplay"):
        return None
    try:
        return await asyncio.create_subprocess_exec(
            "ffplay", "-noborder", "-loglevel", "quiet",
            "-window_title", "Artificial Realities", clip_path,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("Could not launch ffplay for %s", clip_path)
        return None
