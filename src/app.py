"""The app: the story map first, then a film from where the viewer chooses.

`python main.py play` opens the adventure and serves its story map.
Nothing renders and no model is called until the viewer picks where the
story goes on: a timeline's latest position, a story checkpoint (a new
timeline from that moment) or the frame zero (a new timeline from the
first frame; the first time, the adventure itself begins there). The
film plays until the viewer goes back to the map, it has played
`--segments` clips or it fails, and the map is shown again, up to date.
Ctrl+C ends the session; the adventure is saved as of the clip on air.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from src import story_map
from src.adventure import ORIGIN, Adventure, AdventureError
from src.i2v.ltxv import ReferenceRefused
from src.i2v.shape import RendererNotReady
from src.models.config import Config
from src.preflight import check_setup
from src.runner import run_live
from src.util.media import last_frame_png
from src.web import WebPlayer

logger = logging.getLogger(__name__)


class App:
    """One session of one adventure. `state` is where the viewer is: on
    the `map`, a film `starting` (set up, checked) or `playing`; `notice`
    what the map says about the last attempt (empty: nothing to say)."""

    def __init__(self, *, config: Config, adventure: Adventure, log_dir: Path | str,
                 host: str, port: int, max_segments: int | None = None) -> None:
        self.config = config
        self.adventure = adventure
        self.log_dir = Path(log_dir)
        self.max_segments = max_segments
        self.player = WebPlayer(host=host, port=port, app=self)
        self.state = "map"
        self.notice = ""
        self._choices: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._film: asyncio.Task | None = None
        self._stop_asked = False
        # The last still drawn for each timeline's latest position, by its frames' file and time.
        self._heads: dict[str, tuple[tuple[str, float], bytes]] = {}

    async def run(self) -> None:
        await self.player.serve()
        print("That is your story map: continue a timeline, or go back to a moment and take another way. "
              "Ctrl+C ends the session.", flush=True)
        try:
            while True:
                how, target = await self._choices.get()
                await self._play(how, target)
        finally:
            await self.player.close()

    # ── What the page asks ──────────────────────────────────────────

    def status(self) -> dict:
        return {"state": self.state, "notice": self.notice, "timeline": self.adventure.manifest.timeline}

    def map(self) -> dict:
        return story_map.build(self.adventure, playing=self.state != "map",
                               clip_seconds=self.config.clip_seconds).model_dump(mode="json")

    def still(self, key: str) -> tuple[Path | bytes, str] | None:
        """The picture at /still/`key`, found among the adventure's own;
        nothing from the request becomes a path."""
        if key == ORIGIN:
            path = self.adventure.origin_image()
            return (path, mimetypes.guess_type(path.name)[0] or "image/jpeg") if path is not None else None
        known = {t.id for t in self.adventure.timelines()}
        what, _, rest = key.partition("/")
        if what == "head" and rest in known:
            return self._head_still(rest)
        if what in known and rest.isdigit():
            for folder, cp in self.adventure.checkpoints(what):
                if cp.segment == int(rest) and (folder / "still.png").exists():
                    return folder / "still.png", "image/png"
        return None

    def _head_still(self, timeline: str) -> tuple[bytes, str] | None:
        head = self.adventure.head(timeline)
        window = head[0] / "window.mp4" if head is not None else None
        try:
            key = (str(window), window.stat().st_mtime) if window is not None else None
        except OSError:
            return None
        if key is None:
            return None
        cached = self._heads.get(timeline)
        if cached is None or cached[0] != key:
            try:
                cached = (key, last_frame_png(window))
            except Exception as exc:
                logger.warning("No still for the latest position of %s: %s", timeline, exc)
                return None
            self._heads[timeline] = cached
        return cached[1], "image/png"

    def choose(self, how: str, target: str) -> tuple[int, str] | None:
        """The viewer picked where the story goes on: refused (a status and
        why) unless the map is showing and the place is the adventure's."""
        if self.state != "map":
            return 409, "the story is already playing; go back to the map first"
        timelines = self.adventure.timelines()
        if how == "continue" and target not in {t.id for t in timelines}:
            return 404, "there is no such timeline"
        if how == "branch" and target != ORIGIN and target not in {
                f"{t.id}/{cp.segment}" for t in timelines for _, cp in self.adventure.checkpoints(t.id)}:
            return 404, "there is no such checkpoint"
        self.state, self.notice, self._stop_asked = "starting", "", False
        self._choices.put_nowait((how, target))
        self.player.status_changed()
        return None

    def stop(self) -> None:
        """Back to the map: the film ends, saved as of the clip on air."""
        self._stop_asked = True
        if self._film is not None:
            self._film.cancel()

    # ── A film ──────────────────────────────────────────────────────

    async def _play(self, how: str, target: str) -> None:
        try:
            if how == "branch":
                self.adventure.branch(target)
            else:
                self.adventure.enter(target)
        except (AdventureError, OSError, ValueError) as exc:
            self._back(f"Could not go there: {exc}")
            return
        errors = [f.message for f in check_setup(self.config, self.adventure.origin, probe_server=False)
                  if f.level == "error"]
        if errors:
            self._back("The film cannot start: " + " ".join(errors))
            return
        if self._stop_asked:
            self._back("")
            return
        self.state = "playing"
        self.player.status_changed()
        film = asyncio.create_task(run_live(config=self.config, adventure=self.adventure, log_dir=self.log_dir,
                                            player=self.player, max_segments=self.max_segments))
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
        else:
            self._back("")

    def _back(self, notice: str) -> None:
        self.state, self.notice = "map", notice
        if notice:
            print(notice, flush=True)
        self.player.status_changed()
