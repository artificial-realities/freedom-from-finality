"""Web player: the film in a browser, steered while it plays.

`WebPlayer` is a `Player` (see `src.playback.player`) backed by a small
aiohttp server that lasts the whole session (`src.app.App`). The page
shows the picture the story stopped on with one button, Begin the first
time and Continue after that; each film then plays on the same page
(`start` to `stop`) until the viewer stops it. The page plays clips back
to back, holds the last frame while the next clip renders, and has a
text box that posts to the loop at any moment. Without a video backend
the same page shows the film as text at story pace.

The server is the clock: `play` broadcasts what is on air and when it
started, then waits the item's duration. A browser that connects late is
told what is on air and seeks into it. The app hears how many pages are
connected (`pages_changed`), so a film nobody is watching can stop.

Routes:
    GET  /              the film page
    GET  /still         the picture the story stopped on (a video story)
    GET  /events        server-sent events: hello, status, queued, playing, ended, closed
    POST /play          {}: begin the story, or continue it from the last clip seen
    POST /stop          {}: stop the film, saved as of the clip on air
    POST /input         {"text": "..."} (at most MAX_INPUT_CHARS) → the next plan
    GET  /clips/N.mp4   the clip that played as clip N (Range requests supported)

Every POST takes application/json only. While the player listens on
this computer only (the default), it answers only requests addressed to
this computer: a page elsewhere cannot reach it by pointing a name of its
own at 127.0.0.1 (DNS rebinding).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from src.playback.player import Playable, Player

logger = logging.getLogger(__name__)

FILM_PAGE = Path(__file__).with_name("player.html")
HEARTBEAT_S = 15.0
# A viewer input, as long as the page's text box allows. Every input costs
# a Now call, so the server holds the same limit.
MAX_INPUT_CHARS = 400
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WebPlayer(Player):
    """`app` answers what the page asks (`status`, `still`, `play`, `stop`)
    and hears how many pages are connected (`pages_changed`)."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765, app) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.app = app
        self.queued: list[Playable] = []
        self.now: Playable | None = None
        self.started_at: float = 0.0
        self.played = 0
        self._clips: dict[int, Path] = {}
        self._clients: set[asyncio.Queue] = set()
        self._runner: web.AppRunner | None = None
        # No film is on until `start`.
        self._ended = True

    # ── Player ──────────────────────────────────────────────────────

    async def serve(self) -> None:
        """Bring the server up, once."""
        if self._runner is not None:
            return
        app = web.Application(middlewares=[_this_computer] if self.host in LOOPBACK_HOSTS else [])
        app.add_routes([
            web.get("/", self._film_page),
            web.get("/still", self._still),
            web.get("/events", self._events),
            web.post("/play", self._play),
            web.post("/stop", self._stop),
            web.post("/input", self._input),
            web.get("/clips/{clip}.mp4", self._clip),
        ])
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        # Port 0 asks the OS for a free one; report the real one.
        server = getattr(site, "_server", None)
        if server is not None and server.sockets:
            self.port = server.sockets[0].getsockname()[1]
        print(f"Open {self.url}", flush=True)
        if self.host not in LOOPBACK_HOSTS:
            print(f"Note: the player listens on {self.host} and has no login; anyone who can reach port "
                  f"{self.port} can steer the film, and every input costs a Now call.", flush=True)

    async def start(self) -> None:
        """A film begins: nothing of the last one carries over."""
        await self.serve()
        self.queued, self.now, self.started_at, self.played = [], None, 0.0, 0
        self._clips = {}
        self._inputs = asyncio.Queue()
        self._arrived.clear()
        self.taken = []
        self._ended = False
        self.status_changed()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def pages(self) -> int:
        """How many film pages are connected."""
        return len(self._clients)

    def prepare(self, item: Playable) -> None:
        self._register(item)
        self.queued.append(item)
        self._broadcast("queued", self._item_json(item))

    async def play(self, item: Playable) -> None:
        self._register(item)
        # Only what is on air, just before it and queued is served: a clip
        # that has played is deleted, and the film is never replayed.
        for n in [n for n in self._clips if n < item.clip - 1]:
            del self._clips[n]
        self.queued = [q for q in self.queued if q.clip != item.clip]
        self.now = item
        self.started_at = time.time()
        self.played += 1
        self._broadcast("playing", self._item_json(item, started_at=self.started_at))
        await self._hold(item.duration)

    async def stop(self) -> None:
        """The film ended; the server stays up for the next one."""
        self._ended = True
        self.queued, self.now = [], None
        self._broadcast("ended", {"clips": self.played})
        await asyncio.sleep(0)  # let the event reach connected clients

    async def close(self) -> None:
        # Event streams last as long as their pages; end them, or the
        # server would wait for them to close before it can.
        self._broadcast("closed", {})
        await asyncio.sleep(0)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def status_changed(self) -> None:
        """Tell every page where the session is now."""
        self._broadcast("status", self.app.status())

    # ── Handlers ────────────────────────────────────────────────────

    async def _film_page(self, request: web.Request) -> web.Response:
        return web.Response(text=FILM_PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def _still(self, request: web.Request) -> web.StreamResponse:
        # It reads the story's frames from disk, off the loop the film runs on.
        found = await asyncio.to_thread(self.app.still)
        if found is None:
            raise web.HTTPNotFound()
        body, content_type = found
        headers = {"Content-Type": content_type, "Cache-Control": "no-store"}
        if isinstance(body, Path):
            return web.FileResponse(body, headers=headers)
        return web.Response(body=body, headers=headers)

    async def _play(self, request: web.Request) -> web.Response:
        body = await _json_object(request)
        if isinstance(body, web.Response):
            return body
        refused = self.app.play()
        if refused is not None:
            status, error = refused
            return web.json_response({"error": error}, status=status)
        return web.json_response({"ok": True})

    async def _stop(self, request: web.Request) -> web.Response:
        body = await _json_object(request)
        if isinstance(body, web.Response):
            return body
        self.app.stop()
        return web.json_response({"ok": True})

    async def _input(self, request: web.Request) -> web.Response:
        body = await _json_object(request)
        if isinstance(body, web.Response):
            return body
        text = str(body.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "empty input"}, status=400)
        if len(text) > MAX_INPUT_CHARS:
            return web.json_response({"error": f"at most {MAX_INPUT_CHARS} characters"}, status=413)
        if self._ended:
            return web.json_response({"error": "no film is playing"}, status=409)
        self.push_input(text)
        logger.info("viewer input: %s", text)
        return web.json_response({"ok": True})

    async def _clip(self, request: web.Request) -> web.StreamResponse:
        try:
            n = int(request.match_info["clip"])
        except ValueError:
            raise web.HTTPNotFound()
        path = self._clips.get(n)
        if path is None or not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": "video/mp4", "Cache-Control": "max-age=3600"})

    async def _events(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await resp.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._clients.add(queue)
        self.app.pages_changed(self.pages)
        try:
            await _send(resp, "hello", self._snapshot())
            while True:
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
                    continue
                await _send(resp, name, data)
                if name == "closed":
                    break
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            self._clients.discard(queue)
            self.app.pages_changed(self.pages)
        return resp

    # ── Helpers ─────────────────────────────────────────────────────

    def _register(self, item: Playable) -> None:
        if item.path:
            self._clips[item.clip] = Path(item.path)

    def _item_json(self, item: Playable, *, started_at: float | None = None) -> dict:
        data = item.model_dump(exclude={"path"})
        data["url"] = f"/clips/{item.clip}.mp4" if item.path else None
        if started_at is not None:
            data["started_at"] = started_at
        return data

    def _snapshot(self) -> dict:
        return {
            "server_time": time.time(),
            "status": self.app.status(),
            "ended": self._ended,
            "queued": [self._item_json(i) for i in self.queued],
            "now": self._item_json(self.now, started_at=self.started_at) if self.now else None,
        }

    def _broadcast(self, name: str, data: dict) -> None:
        for queue in list(self._clients):
            queue.put_nowait((name, data))


@web.middleware
async def _this_computer(request: web.Request, handler):
    """Refuse a request whose `Host` is not this computer."""
    try:
        host = urlsplit(f"//{request.host}").hostname or ""
    except ValueError:
        host = ""
    if host not in LOOPBACK_HOSTS:
        return web.json_response({"error": "the player answers only requests to this computer"}, status=403)
    return await handler(request)


async def _json_object(request: web.Request) -> dict | web.Response:
    """The request's JSON object, or the response refusing it. JSON only: a
    browser cannot send it cross-site without a CORS preflight, which this
    server never grants, so another page open in the viewer's browser can
    neither type into the film nor start or stop it."""
    if request.content_type != "application/json":
        return web.json_response({"error": "expected application/json"}, status=415)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "expected a JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected a JSON object"}, status=400)
    return body


async def _send(resp: web.StreamResponse, name: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    await resp.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
