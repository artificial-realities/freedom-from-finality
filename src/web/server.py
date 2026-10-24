"""Web player: the story map, and the film in a browser, steered while it plays.

`WebPlayer` is a `Player` (see `src.playback.player`) backed by a small
aiohttp server. With an `app` (`src.app.App`) the server lasts the whole
session: it opens on the story map, and each film the viewer chooses
plays on the film page (`start` to `stop`) and then hands back to the
map. The film page plays clips back to back, holds the last frame while
the next clip renders, and has a text box that posts to the loop at any
moment. Without a video backend the same page shows the film as text at
story pace.

The server is the clock: `play` broadcasts what is on air and when it
started, then waits the item's duration. A browser that connects late is
told what is on air and seeks into it.

Routes:
    GET  /              the story map
    GET  /film          the film page
    GET  /map.json      the story map's data (`src.story_map`)
    GET  /still/KEY     a picture on the map: origin, <timeline>/<segment>, head/<timeline>
    GET  /events        server-sent events: hello, status, queued, playing, ended, closed
    POST /continue      {"timeline": id}: play that timeline on from its latest position
    POST /branch        {"checkpoint": id}: a new timeline from that checkpoint ("origin": the frame zero)
    POST /stop          end the film; the page goes back to the map
    POST /input         {"text": "..."} (at most MAX_INPUT_CHARS) → the next plan
    GET  /clips/N.mp4   the clip that played for segment N (Range requests supported)

Every POST takes application/json only. The ids it names are looked up
among the adventure's own timelines and checkpoints; none becomes a path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from src.playback.player import Playable, Player

logger = logging.getLogger(__name__)

FILM_PAGE = Path(__file__).with_name("player.html")
MAP_PAGE = Path(__file__).with_name("map.html")
HEARTBEAT_S = 15.0
# A viewer line, as long as the page's text box allows. Every line costs a
# Now call, so the server holds the same limit.
MAX_INPUT_CHARS = 400
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WebPlayer(Player):
    """`app` answers what the map asks (`status`, `map`, `still`, `choose`,
    `stop`); without one the server is the film's alone and closes with it."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765, app=None) -> None:
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
        # No film is on until `start`, when the map comes first.
        self._ended = app is not None

    # ── Player ──────────────────────────────────────────────────────

    async def serve(self) -> None:
        """Bring the server up, once."""
        if self._runner is not None:
            return
        app = web.Application()
        app.add_routes([
            web.get("/", self._map_page if self.app is not None else self._film_page),
            web.get("/film", self._film_page),
            web.get("/map.json", self._map),
            web.get("/still/{key:.+}", self._still),
            web.get("/events", self._events),
            web.post("/continue", self._continue),
            web.post("/branch", self._branch),
            web.post("/stop", self._stop),
            web.post("/input", self._input),
            web.get("/clips/{turn}.mp4", self._clip),
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
                  f"{self.port} can steer the film, and every line costs a Now call.", flush=True)

    async def start(self, *, session_id: str) -> None:
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

    def prepare(self, item: Playable) -> None:
        self._register(item)
        self.queued.append(item)
        self._broadcast("queued", self._item_json(item))

    async def play(self, item: Playable) -> None:
        self._register(item)
        # Only what is on air, just before it and queued is served: a clip
        # that has played is deleted, and the film is never replayed.
        for turn in [t for t in self._clips if t < item.turn - 1]:
            del self._clips[turn]
        self.queued = [q for q in self.queued if q.turn != item.turn]
        self.now = item
        self.started_at = time.time()
        self.played += 1
        self._broadcast("playing", self._item_json(item, started_at=self.started_at))
        await self._hold(item.duration)

    async def stop(self) -> None:
        """The film ended. The server stays up for the map, unless it is the film's alone."""
        self._ended = True
        self._broadcast("ended", {"segments": self.played})
        await asyncio.sleep(0)  # let the event reach connected clients
        if self.app is None:
            await self.close()

    async def close(self) -> None:
        # Event streams last as long as their pages; end them, or the
        # server would wait for them to close before it can.
        self._broadcast("closed", {})
        await asyncio.sleep(0)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def status_changed(self) -> None:
        """Tell every page where the session is now (the map, or a film)."""
        self._broadcast("status", self._status())

    # ── Handlers ────────────────────────────────────────────────────

    async def _film_page(self, request: web.Request) -> web.Response:
        return web.Response(text=FILM_PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def _map_page(self, request: web.Request) -> web.Response:
        return web.Response(text=MAP_PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def _map(self, request: web.Request) -> web.Response:
        if self.app is None:
            raise web.HTTPNotFound()
        # It reads the adventure from disk, off the loop the film runs on.
        data = await asyncio.to_thread(self.app.map)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def _still(self, request: web.Request) -> web.StreamResponse:
        if self.app is None:
            raise web.HTTPNotFound()
        found = await asyncio.to_thread(self.app.still, request.match_info["key"])
        if found is None:
            raise web.HTTPNotFound()
        body, content_type = found
        headers = {"Content-Type": content_type, "Cache-Control": "no-cache"}
        if isinstance(body, Path):
            return web.FileResponse(body, headers=headers)
        return web.Response(body=body, headers=headers)

    async def _continue(self, request: web.Request) -> web.Response:
        body = await _json_object(request)
        if isinstance(body, web.Response):
            return body
        return self._choose("continue", str(body.get("timeline", "")))

    async def _branch(self, request: web.Request) -> web.Response:
        body = await _json_object(request)
        if isinstance(body, web.Response):
            return body
        return self._choose("branch", str(body.get("checkpoint", "")))

    def _choose(self, how: str, target: str) -> web.Response:
        if self.app is None:
            raise web.HTTPNotFound()
        refused = self.app.choose(how, target)
        if refused is not None:
            status, error = refused
            return web.json_response({"error": error}, status=status)
        return web.json_response({"ok": True})

    async def _stop(self, request: web.Request) -> web.Response:
        body = await _json_object(request)
        if isinstance(body, web.Response):
            return body
        if self.app is None:
            raise web.HTTPNotFound()
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
            turn = int(request.match_info["turn"])
        except ValueError:
            raise web.HTTPNotFound()
        path = self._clips.get(turn)
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
        try:
            await _send(resp, "hello", self._snapshot())
            while True:
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
                    continue
                await _send(resp, name, data)
                if name == "closed" or (name == "ended" and self.app is None):
                    break
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            self._clients.discard(queue)
        return resp

    # ── Helpers ─────────────────────────────────────────────────────

    def _register(self, item: Playable) -> None:
        if item.path:
            self._clips[item.turn] = Path(item.path)

    def _item_json(self, item: Playable, *, started_at: float | None = None) -> dict:
        data = item.model_dump(exclude={"path"})
        data["url"] = f"/clips/{item.turn}.mp4" if item.path else None
        if started_at is not None:
            data["started_at"] = started_at
        return data

    def _status(self) -> dict:
        return self.app.status() if self.app is not None else {"state": "film"}

    def _snapshot(self) -> dict:
        return {
            "server_time": time.time(),
            "status": self._status(),
            "ended": self._ended,
            "queued": [self._item_json(i) for i in self.queued],
            "now": self._item_json(self.now, started_at=self.started_at) if self.now else None,
        }

    def _broadcast(self, name: str, data: dict) -> None:
        for queue in list(self._clients):
            queue.put_nowait((name, data))


async def _json_object(request: web.Request) -> dict | web.Response:
    """The request's JSON object, or the response refusing it. JSON only: a
    browser cannot send it cross-site without a CORS preflight, which this
    server never grants, so another page open in the viewer's browser can
    neither type into the film nor steer the map."""
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
