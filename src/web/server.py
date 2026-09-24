"""Web player: the film in a browser, steered while it plays.

`WebPlayer` is a `Player` (see `src.playback.player`) backed by a small
aiohttp server. The page it serves plays clips back to back, holds the
last frame while the next segment renders, and has a text box that posts
to the loop at any moment. Without a video backend the same page plays the
film as text at story pace, so a headless config gives a working session.

The server is the clock: `play` broadcasts what is on air and when it
started, then waits the item's duration. A browser that connects late gets
the whole history in its first event and seeks into the current item.

Routes:
    GET  /              the player page
    GET  /events        server-sent events: hello, queued, playing, input, status, ended
    POST /input         {"text": "..."} → queued for the next segment
    POST /beacon        {"kind": "swap" | "loadstart_on_playing" | "waiting", ...}: the
                        page's own timing, written to the run's timing log
    GET  /clips/N.mp4   the clip that played for segment N (Range requests supported)
    GET  /state         JSON snapshot (what /events sends as `hello`)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path

from aiohttp import web

from src.playback.player import Playable, Player

logger = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("player.html")
HEARTBEAT_S = 15.0
# Browser timing beacons: the kinds the page sends, the fields kept from
# each (anything else is dropped), and the largest body accepted.
BEACON_KINDS = {"swap", "loadstart_on_playing", "waiting"}
BEACON_NUMBERS = ("turn", "ms", "media_time", "offset")
BEACON_FLAGS = ("armed", "prefetched")
BEACON_MAX_BYTES = 2048


class WebPlayer(Player):
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        time_scale: float = 1.0,
        seam: str = "fade",
    ) -> None:
        super().__init__(time_scale=time_scale)
        self.host = host
        self.port = port
        # How the page joins clips (the config's `seam`): "fade" (a 250 ms
        # crossfade of two elements) or "overlap" (AUD-02 / LTX-12, D-03: a
        # pool of three elements, the picture cut at the outgoing clip's
        # media time where the next clip's overlap begins, an equal-power
        # audio crossfade over the overlap).
        if seam not in ("fade", "overlap"):
            raise ValueError(f"seam must be 'fade' or 'overlap', not {seam!r}")
        self.seam = seam
        self.title = ""
        self.session_id = ""
        self.status_text = ""
        self.history: list[Playable] = []
        self.queued: list[Playable] = []
        self.now: Playable | None = None
        self.started_at: float = 0.0
        self.inputs: list[dict] = []
        self._clips: dict[int, Path] = {}
        self._clients: set[asyncio.Queue] = set()
        self._runner: web.AppRunner | None = None
        self._ended = False

    # ── Player ──────────────────────────────────────────────────────

    async def start(self, *, title: str, session_id: str) -> None:
        self.title = title
        self.session_id = session_id
        app = web.Application()
        app.add_routes([
            web.get("/", self._page),
            web.get("/events", self._events),
            web.post("/input", self._input),
            web.post("/beacon", self._beacon),
            web.get("/state", self._state),
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
        print(f"Player: {self.url}  (type there to steer the story)", flush=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def prepare(self, item: Playable) -> None:
        self._register(item)
        self.queued.append(item)
        self._broadcast("queued", self._item_json(item))

    def status(self, text: str) -> None:
        self.status_text = text
        self._broadcast("status", {"text": text})

    async def play(self, item: Playable) -> None:
        self._register(item)
        self.queued = [q for q in self.queued if q.turn != item.turn]
        self.now = item
        self.started_at = time.time()
        self.history.append(item)
        self._broadcast("playing", self._item_json(item, started_at=self.started_at))
        await self._hold(item.duration)

    async def stop(self) -> None:
        self._ended = True
        self.status_text = "the film has ended"
        self._broadcast("ended", {"segments": len(self.history)})
        await asyncio.sleep(0)  # let the event reach connected clients
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ── Handlers ────────────────────────────────────────────────────

    async def _page(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def _state(self, request: web.Request) -> web.Response:
        return web.json_response(self._snapshot())

    async def _input(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "expected a JSON body"}, status=400)
        text = str(body.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "empty input"}, status=400)
        if self._ended:
            return web.json_response({"error": "the film has ended"}, status=409)
        record = {"text": text, "received_at": time.time(),
                  "after_turn": self.history[-1].turn if self.history else 0}
        self.inputs.append(record)
        self.push_input(text)
        self._broadcast("input", record)
        logger.info("viewer input: %s", text)
        return web.json_response({"ok": True, **record})

    async def _beacon(self, request: web.Request) -> web.Response:
        """A timing beacon from the page (`navigator.sendBeacon`, so the body
        is JSON sent as text/plain). Only known kinds and fields pass."""
        text = await request.text()
        if len(text) > BEACON_MAX_BYTES:
            return web.json_response({"error": "beacon too large"}, status=413)
        try:
            body = json.loads(text)
        except ValueError:
            return web.json_response({"error": "expected a JSON body"}, status=400)
        if not isinstance(body, dict) or body.get("kind") not in BEACON_KINDS:
            return web.json_response({"error": "unknown beacon"}, status=400)
        record: dict = {"kind": body["kind"]}
        for key in BEACON_NUMBERS:
            value = body.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                record[key] = value
        for key in BEACON_FLAGS:
            if isinstance(body.get(key), bool):
                record[key] = body[key]
        if self.on_beacon is not None:
            self.on_beacon(record)
        return web.Response(status=204)

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
        data = item.model_dump()
        data["url"] = f"/clips/{item.turn}.mp4" if item.path else None
        if started_at is not None:
            data["started_at"] = started_at
        return data

    def _snapshot(self) -> dict:
        return {
            "title": self.title,
            "session": self.session_id,
            "seam": self.seam,
            "server_time": time.time(),
            "status": self.status_text,
            "ended": self._ended,
            "history": [self._item_json(i) for i in self.history],
            "queued": [self._item_json(i) for i in self.queued],
            "now": self._item_json(self.now, started_at=self.started_at) if self.now else None,
            "inputs": list(self.inputs),
        }

    def _broadcast(self, name: str, data: dict) -> None:
        for queue in list(self._clients):
            queue.put_nowait((name, data))


async def _send(resp: web.StreamResponse, name: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    await resp.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
