"""RUN-10 (D-12): after a server restart the client sends the last clip's
tail frames so the restarted server continues the picture (the default,
`ltxv_restart_recovery: "window"`), or re-seeds from the reference. A clip
restored from the window is a continuation (the overlap seam still
applies); a re-seeded one is not, and the Director is told."""

from __future__ import annotations

import base64
import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from src.i2v import ltxv
from src.i2v.shape import ClipShape
from src.models.config import Config

W, H = 64, 32


def _clip(path: Path, frames: int = 17) -> bytes:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", f"testsrc=s={W}x{H}:r=24",
                    "-frames:v", str(frames), "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


class _Server:
    """A fake LTX server that forgets its session once (a restart) before
    the `restart_at`-th request."""

    def __init__(self, clip: bytes, restart_at: int) -> None:
        self.clip, self.restart_at, self.posts, self.fresh = clip, restart_at, [], True

    def __enter__(self):
        server = self

        class _Resp:
            def __init__(self, status=200, body=b"", js=None, text=""):
                self.status_code, self.content, self._js, self.text = status, body, js or {}, text
                self.headers = {"X-Session-Epoch": "a1" if len(server.posts) < server.restart_at else "b2"}

            def json(self):
                return self._js

        def get(url, timeout=0):
            return _Resp(js={"status": "ready", "frame_rate": 24, "width": W, "height": H})

        def post(url, json=None, timeout=0):
            server.posts.append(dict(json))   # the client reuses its payload dict for the retry
            if len(server.posts) == server.restart_at:
                server.fresh = True
            if server.fresh and not json.get("reference_image"):
                return _Resp(400, text="reference_image required on first call (base64 PNG).")
            server.fresh = False
            return _Resp(body=server.clip)

        self._orig = ltxv.requests.get, ltxv.requests.post
        ltxv.requests.get, ltxv.requests.post = get, post
        return self

    def __exit__(self, *exc):
        ltxv.requests.get, ltxv.requests.post = self._orig


def _run(recovery: str) -> tuple[list[dict], list[dict]]:
    with tempfile.TemporaryDirectory() as d:
        clip = _clip(Path(d) / "c.mp4")
        ref = Path(d) / "ref.png"
        Image.new("RGB", (W, H), "white").save(ref)
        client = ltxv.LtxvI2V(server_url="http://x", output_dir=d, restart_recovery=recovery)
        renders = []
        with _Server(clip, restart_at=3) as server:
            for turn in (1, 2, 3):
                assert client._synthesize_sync(str(ref), f"p{turn}", turn, ClipShape.from_seconds(1))
                renders.append(dict(client.last_render))
        return server.posts, renders


def test_the_window_arm_continues_from_the_last_clip():
    posts, renders = _run("window")
    retry = posts[-1]
    assert posts[-2].get("reference_image") is None, "turn 3's first try: the server has restarted"
    frames = retry["window_frames"]
    assert len(frames) == 9 and retry.get("reference_image"), "the last clip's 9 tail frames, and the reference"
    assert Image.open(io.BytesIO(base64.b64decode(frames[0]))).size == (W, H)
    assert renders[-1]["reference"] is False and renders[-1]["restored"] == 9
    assert renders[-1]["session_epoch"] == "b2" and renders[0]["session_epoch"] == "a1"


def test_the_default_re_seeds_from_the_reference():
    posts, renders = _run("reference")
    assert "window_frames" not in posts[-1] and posts[-1].get("reference_image")
    assert renders[-1]["reference"] is True and renders[-1]["reseeded"] is True


def test_the_window_is_the_default_and_named_values_only():
    assert Config(name="t", llm_backend="mock", model="m").ltxv_restart_recovery == "window"
    assert Config.from_yaml("configs/director.live.yaml").ltxv_restart_recovery == "window"
    assert ltxv.LtxvI2V(server_url="http://x").restart_recovery == "window"
    for bad in (lambda: Config(name="t", llm_backend="mock", model="m", ltxv_restart_recovery="latest"),
                lambda: ltxv.LtxvI2V(server_url="http://x", restart_recovery="latest")):
        try:
            bad()
            raise AssertionError("an unknown recovery is refused")
        except ValueError:
            pass


async def test_the_director_hears_of_a_reseed():
    """When the picture was re-seeded from the reference anyway, the next
    plan's picture section says so, once."""
    from src.graph.director_engine import DirectorEngine
    from src.state.story_state import StoryState
    from src.models.story import Story
    from tests.test_story_authority import Recording
    from tests.test_showrunner_clock import _beat, _engine
    engine, _, state = _engine([])
    stub = Recording([_beat("wake")])
    engine.llm = engine.llm_director = engine.llm_showrunner = stub
    for seg in (1, 2):
        state.turn_number = seg
        if not engine.has_next_clip():
            await engine.plan(state)
        await engine.emit(state)
    engine.note_reseed(2)
    for seg in (3, 4):
        state.turn_number = seg
        if not engine.has_next_clip():
            await engine.plan(state)
        await engine.emit(state)
    said = [p for p in stub.director_prompts if "re-seeded from the reference image" in p]
    assert len(said) == 1 and "At segment 2" in said[0] and "Lego Man is back in the reference pose" in said[0]
