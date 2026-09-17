"""The OpenAI backend forwards the configured processing tier; other
backends ignore it."""

from __future__ import annotations

from src.llm import build_backend
from src.llm.openai_backend import OpenAIBackend
from src.story.schemas import BeatPlan


class _Msg:
    content = '{"intent": "a wave", "framing": "wide", "clips": [{"action": "He waves.", "end_state": "e", "on_screen": ["lego_man"]}]}'


class _Choice:
    message = _Msg()


class _Resp:
    choices = [_Choice()]
    usage = None


async def test_openai_backend_forwards_service_tier():
    captured: list[dict] = []

    async def fake_create(**kwargs):
        captured.append(kwargs)
        return _Resp()

    tiered = OpenAIBackend(model="gpt-4.1", api_key="test", service_tier="priority")
    tiered.client.chat.completions.create = fake_create
    plain = OpenAIBackend(model="gpt-4.1", api_key="test")
    plain.client.chat.completions.create = fake_create
    messages = [{"role": "user", "content": "hi"}]
    await tiered.generate_structured(messages, BeatPlan)
    await tiered.generate(messages)
    await plain.generate_structured(messages, BeatPlan)
    assert captured[0]["service_tier"] == "priority" and captured[1]["service_tier"] == "priority"
    assert "service_tier" not in captured[2], "unset: the API default, nothing sent"
    assert captured[0]["response_format"]["json_schema"]["strict"] is True
    build_backend("mock", "mock", service_tier="priority")  # ignored, no error
