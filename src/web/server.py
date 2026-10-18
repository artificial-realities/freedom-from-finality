"""Web player: the film in a browser, steered while it plays.

`WebPlayer` is a `Player` (see `src.playback.player`) backed by a small
aiohttp server. The page it serves plays clips back to back, holds the
last frame while the next clip renders, and has a text box that posts to
the loop at any moment. Without a video backend the same page shows the
film as text at story pace.

The server is the clock: `play` broadcasts what is on air and when it
started, then waits the item's duration. A browser that connects late is
told what is on air and seeks into it.

Routes:
    GET  /              the player page
    GET  /events        server-sent events: hello, queued, playing, ended
    POST /input         {"text": "..."} (application/json, at most MAX_INPUT_CHARS) → the next plan
    GET  /clips/N.mp4   the clip that played for segment N (Range requests supported)
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

PAGE = Path(__file__).with_name("player.html")
HEARTBEAT_S = 15.0
# A viewer line, as long as the page's text box allows. Every line costs a
# Director call, so the server holds the same limit.
MAX_INPUT_CHARS = 400
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WebPlayer(Player):
    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765, time_scale: float = 1.0) -> None:
        super().__init__(time_scale=time_scale)
        self.host = host
        self.port = port
        self.title = ""
        self.queued: list[Playable] = []
        self.now: Playable | None = None
        self.started_at: float = 0.0
        self.played = 0
        self._clips: dict[int, Path] = {}
        self._clients: set[asyncio.Queue] = set()
        self._runner: web.AppRunner | None = None
        self._ended = False

    # ── Player ──────────────────────────────────────────────────────

    async def start(self, *, title: str, session_id: str) -> None:
        self.title = title
        app = web.Application()
        app.add_routes([
            web.get("/", self._page),
            web.get("/events", self._events),
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
        print(f"Player: {self.url}", flush=True)
        if self.host not in LOOPBACK_HOSTS:
            print(f"Note: the player listens on {self.host} and has no login; anyone who can reach port "
                  f"{self.port} can steer the film, and every line costs a Director call.", flush=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def prepare(self, item: Playable) -> None:
        self._register(item)
        self.queued.append(item)
        self._broadcast("queued", self._item_json(item))

    async def play(self, item: Playable) -> None:
        self._register(item)
        self.queued = [q for q in self.queued if q.turn != item.turn]
        self.now = item
        self.started_at = time.time()
        self.played += 1
        self._broadcast("playing", self._item_json(item, started_at=self.started_at))
        await self._hold(item.duration)

    async def stop(self) -> None:
        self._ended = True
        self._broadcast("ended", {"segments": self.played})
        await asyncio.sleep(0)  # let the event reach connected clients
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ── Handlers ────────────────────────────────────────────────────

    async def _page(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def _input(self, request: web.Request) -> web.Response:
        # JSON only: a browser cannot send it cross-site without a CORS
        # preflight, which this server never grants, so another page open in
        # the viewer's browser cannot type into the film.
        if request.content_type != "application/json":
            return web.json_response({"error": "expected application/json"}, status=415)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "expected a JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "expected a JSON object"}, status=400)
        text = str(body.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "empty input"}, status=400)
        if len(text) > MAX_INPUT_CHARS:
            return web.json_response({"error": f"at most {MAX_INPUT_CHARS} characters"}, status=413)
        if self._ended:
            return web.json_response({"error": "the film has ended"}, status=409)
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
                if name == "ended":
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

    def _snapshot(self) -> dict:
        return {
            "title": self.title,
            "server_time": time.time(),
            "ended": self._ended,
            "queued": [self._item_json(i) for i in self.queued],
            "now": self._item_json(self.now, started_at=self.started_at) if self.now else None,
        }

    def _broadcast(self, name: str, data: dict) -> None:
        for queue in list(self._clients):
            queue.put_nowait((name, data))


async def _send(resp: web.StreamResponse, name: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    await resp.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
