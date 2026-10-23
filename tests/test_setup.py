"""What someone running a clone meets first: the setup check, a missing key
refused in a sentence, the server address from the environment, and the
real OpenAI client against a local stand-in of the API (so an SDK upgrade
that breaks the call shows here, not in a paid run)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import socket
from pathlib import Path

from aiohttp import web

import main
from src.llm.openai_backend import OpenAIBackend
from src.models.config import Config
from src.story.schemas import BeatPlan

FAKE_KEY = "sk-test-not-a-real-key-0123456789"


def _args(config: str, **over) -> argparse.Namespace:
    base = dict(config=Path(config), story=Path("stories/lego-void"), log_dir=Path("logs"),
                host="127.0.0.1", port=0, segments=None, verbose=False)
    base.update(over)
    return argparse.Namespace(**base)


def _check(config: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main.cmd_check(_args(config))
    return code, out.getvalue()


@contextlib.asynccontextmanager
async def _serve(routes):
    """An aiohttp app on a free loopback port; yields its base URL."""
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_server_address_comes_from_the_environment(monkeypatch):
    assert Config.from_yaml("configs/video.yaml").ltxv_server_url == "http://localhost:8000"
    monkeypatch.setenv("LTXV_SERVER_URL", "http://gpu-box:9000")
    assert Config.from_yaml("configs/video.yaml").ltxv_server_url == "http://gpu-box:9000"


def test_a_config_names_only_known_settings(tmp_path):
    (tmp_path / "old.yaml").write_text("director_model: x\nseam: fade\n")
    try:
        Config.from_yaml(tmp_path / "old.yaml")
        raise AssertionError("an unknown setting must be refused")
    except ValueError as exc:
        assert "director_model" in str(exc) or "seam" in str(exc)
    assert Config.from_yaml("configs/text.yaml").name == "text", "a config is named after its file"


def test_check_names_a_missing_key_and_never_shows_one(monkeypatch):
    code, out = _check("configs/text.yaml")
    assert code == 1 and "OPENAI_API_KEY is not set" in out and "platform.openai.com/api-keys" in out
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    code, out = _check("configs/text.yaml")
    assert code == 0 and "OPENAI_API_KEY is set" in out and "Ready to play" in out
    assert FAKE_KEY not in out and FAKE_KEY[:12] not in out, "a key is reported as set, never shown"


async def test_play_refuses_to_start_without_a_key(tmp_path):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await main.cmd_play(_args("configs/text.yaml", log_dir=tmp_path))
    assert code == 1 and "OPENAI_API_KEY is not set" in err.getvalue()
    assert not (tmp_path / "sessions").exists(), "nothing ran"


async def test_check_probes_the_video_server(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LTXV_SERVER_URL", f"http://127.0.0.1:{_closed_port()}")
    code, out = _check("configs/video.yaml")
    assert code == 1 and "not reachable" in out

    async def health(request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ready", "frame_rate": 24.0, "warm_frames": [153], "width": 512, "height": 288,
            "gpu": "Test GPU", "audio_ceiling_lufs": -18.0,
        })

    async with _serve([web.get("/health", health)]) as url:
        monkeypatch.setenv("LTXV_SERVER_URL", url)
        code, out = await asyncio.to_thread(_check, "configs/video.yaml")
        assert code == 0 and "ready on Test GPU" in out, out


async def test_the_openai_backend_against_a_local_api(monkeypatch):
    """Spark's call as the installed SDK sends it: strict JSON schema,
    reasoning effort, completion-token cap; the reply's text comes back. A
    refused key is an error result, not an exception."""
    seen: list[dict] = []

    async def completions(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {FAKE_KEY}":
            return web.json_response({"error": {"message": "Incorrect API key", "type": "invalid_request_error",
                                                "code": "invalid_api_key"}}, status=401)
        seen.append(await request.json())
        return web.json_response({
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    async with _serve([web.post("/v1/chat/completions", completions)]) as url:
        monkeypatch.setenv("OPENAI_BASE_URL", f"{url}/v1")
        backend = OpenAIBackend(model="test-model", api_key=FAKE_KEY, reasoning_effort="none")
        text, usage = await backend.generate_structured(
            [{"role": "user", "content": "hi"}], BeatPlan, max_tokens=64)
        assert json.loads(text) == {"ok": True} and usage == {"prompt": 3, "completion": 2, "total": 5}
        body = seen[0]
        assert body["model"] == "test-model" and body["reasoning_effort"] == "none"
        assert body["max_completion_tokens"] == 64 and "temperature" not in body
        schema = body["response_format"]["json_schema"]
        assert body["response_format"]["type"] == "json_schema" and schema["name"] == "BeatPlan"
        assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False

        refused = OpenAIBackend(model="test-model", api_key="sk-wrong")
        text, usage = await refused.generate_structured([{"role": "user", "content": "hi"}], BeatPlan)
        assert text == "" and "error" in usage
