"""The app: the film page, and a film each time the viewer begins or
continues the story.

`python main.py play` opens the viewer's story and serves the film page.
Nothing renders and no model is called until the viewer presses Begin
(the first time) or Continue. The film plays until the viewer stops it,
no page has been open for PAGELESS_STOP_S seconds, it has played
`--clips` clips or it fails; then the page offers Continue again. Ctrl+C
ends the session; the story is saved as of the clip on air.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from src.i2v.ltxv import ReferenceRefused
from src.i2v.shape import RendererNotReady
from src.models.config import Config
from src.preflight import check_setup
from src.runner import run_live
from src.story_folder import StoryFolder, StoryFolderError
from src.util.media import last_frame_png
from src.web import WebPlayer

logger = logging.getLogger(__name__)

# A film nobody is watching stops after this long without a page open, so
# a closed tab does not keep it running (and calling Now). It says nothing
# about the viewer: nothing of it reaches the story or their memory.
PAGELESS_STOP_S = 60.0


class App:
    """One session of the viewer's story. `film` is where it is: `stopped`
    (the page offers Begin or Continue), `starting` (set up, checked) or
    `playing`; `notice` what the page says about the last film (empty:
    nothing to say)."""

    def __init__(self, *, config: Config, folder: StoryFolder, log_dir: Path | str,
                 host: str, port: int, max_clips: int | None = None) -> None:
        self.config = config
        self.folder = folder
        self.log_dir = Path(log_dir)
        self.max_clips = max_clips
        self.player = WebPlayer(host=host, port=port, app=self)
        self.film = "stopped"
        self.notice = ""
        self._requests: asyncio.Queue[None] = asyncio.Queue()
        self._film: asyncio.Task | None = None
        self._stop_asked = False
        self._pageless: asyncio.TimerHandle | None = None
        self._pageless_stop = False
        # The still last drawn from the saved frames, by their file and time.
        self._still: tuple[tuple[str, float], bytes] | None = None

    async def run(self) -> None:
        await self.player.serve()
        print("Press Begin (or Continue) on that page to play your story. Ctrl+C ends the session.", flush=True)
        try:
            while True:
                await self._requests.get()
                await self._play()
        finally:
            await self.player.close()

    # ── What the page asks ──────────────────────────────────────────

    def status(self) -> dict:
        last = self.folder.last
        return {"film": self.film, "notice": self.notice, "clip": last.clip if last else 0,
                "still": self.config.video_enabled, "last": last.action if last else ""}

    def still(self) -> tuple[Path | bytes, str] | None:
        """The picture the story stopped on: the last of the frames saved
        with the last clip seen, or Frame Zero's image before the first."""
        if not self.config.video_enabled:
            return None
        frames = self.folder.last_frames
        if frames is None:
            path = self.folder.frame_zero_image()
            return (path, mimetypes.guess_type(path.name)[0] or "image/png") if path is not None else None
        try:
            key = (str(frames), frames.stat().st_mtime)
        except OSError:
            return None
        if self._still is None or self._still[0] != key:
            try:
                self._still = (key, last_frame_png(frames))
            except Exception as exc:
                logger.warning("No still of the saved frames: %s", exc)
                return None
        return self._still[1], "image/png"

    def play(self) -> tuple[int, str] | None:
        """The viewer pressed Begin or Continue: refused (a status and why)
        while a film is already on."""
        if self.film != "stopped":
            return 409, "the film is already playing"
        self.film, self.notice, self._stop_asked, self._pageless_stop = "starting", "", False, False
        self._requests.put_nowait(None)
        self.player.status_changed()
        return None

    def stop(self) -> None:
        """The film ends, saved as of the clip on air."""
        self._stop_asked = True
        if self._film is not None:
            self._film.cancel()

    def pages_changed(self, pages: int) -> None:
        """A film page connected or went away. With none left the film
        stops after PAGELESS_STOP_S, unless one comes back first."""
        if pages > 0 or self.film == "stopped":
            if self._pageless is not None:
                self._pageless.cancel()
                self._pageless = None
            return
        if self._pageless is None:
            self._pageless = asyncio.get_running_loop().call_later(PAGELESS_STOP_S, self._nobody_watching)

    def _nobody_watching(self) -> None:
        self._pageless = None
        if self.film != "stopped":
            logger.info("No page has been open for %.0f s; stopping the film", PAGELESS_STOP_S)
            self._pageless_stop = True
            self.stop()

    # ── A film ──────────────────────────────────────────────────────

    async def _play(self) -> None:
        try:
            # From disk: nothing a film prepared beyond the screen carries over.
            self.folder.reload()
        except (StoryFolderError, OSError, ValueError) as exc:
            self._back(f"Could not open your story: {exc}")
            return
        errors = [f.message for f in check_setup(self.config, self.folder.frame_zero, probe_server=False)
                  if f.level == "error"]
        if errors:
            self._back("The film cannot start: " + " ".join(errors))
            return
        if self._stop_asked:
            self._back("")
            return
        self.film = "playing"
        self.player.status_changed()
        self.pages_changed(self.player.pages)
        film = asyncio.create_task(run_live(config=self.config, folder=self.folder, log_dir=self.log_dir,
                                            player=self.player, max_clips=self.max_clips))
        self._film = film
        try:
            await asyncio.wait({film})
        except asyncio.CancelledError:
            # The session ends (Ctrl+C): the film saves as of the clip on air first.
            film.cancel()
            await asyncio.gather(film, return_exceptions=True)
            raise
        finally:
            self._film = None
        exc = None if film.cancelled() else film.exception()
        if isinstance(exc, (RendererNotReady, ReferenceRefused)):
            self._back(f"The film cannot start: {exc}")
        elif exc is not None:
            logger.error("The film stopped", exc_info=exc)
            self._back(f"The film stopped: {exc}")
        elif self._pageless_stop:
            self._back(f"The film stopped after {PAGELESS_STOP_S:.0f} s with no page open; "
                       "your story is saved as of the clip on air.")
        else:
            self._back("")

    def _back(self, notice: str) -> None:
        self.film, self.notice = "stopped", notice
        if notice:
            print(notice, flush=True)
        self.pages_changed(self.player.pages)
        self.player.status_changed()
