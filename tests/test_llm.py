"""The OpenAI backend forwards the configured processing tier (other
backends ignore it) and, for a reasoning model, its effort (RUN-16)."""

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


async def test_a_reasoning_model_gets_its_effort_and_completion_tokens():
    """RUN-16: the GPT-5 family refuses `max_tokens` and a temperature;
    with `reasoning_effort` set the backend sends the effort and
    `max_completion_tokens` instead, and a chat model is unchanged."""
    from src.graph.director_engine import DirectorEngine
    from src.models.config import Config
    captured: list[dict] = []

    async def fake_create(**kwargs):
        captured.append(kwargs)
        return _Resp()

    reasoning = OpenAIBackend(model="gpt-5.4-mini", api_key="test", service_tier="priority", reasoning_effort="none")
    reasoning.client.chat.completions.create = fake_create
    chat = OpenAIBackend(model="gpt-4.1", api_key="test")
    chat.client.chat.completions.create = fake_create
    messages = [{"role": "user", "content": "hi"}]
    await reasoning.generate_structured(messages, BeatPlan, max_tokens=2048)
    await reasoning.generate(messages, max_tokens=512)
    await chat.generate_structured(messages, BeatPlan, temperature=0.7, max_tokens=2048)
    for call in captured[:2]:
        assert call["reasoning_effort"] == "none" and "max_tokens" not in call and "temperature" not in call
    assert captured[0]["max_completion_tokens"] == 2048 and captured[1]["max_completion_tokens"] == 512
    assert captured[2]["max_tokens"] == 2048 and captured[2]["temperature"] == 0.7 and "reasoning_effort" not in captured[2]

    import os
    saved = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = saved or "test"        # the engine builds its backend from the environment
    try:
        config = Config(name="t", llm_backend="openai", model="gpt-4.1", director_model="gpt-5.4-mini",
                        director_reasoning_effort="none")
        engine = DirectorEngine(llm=chat, config=config)
    finally:
        if saved is None:
            del os.environ["OPENAI_API_KEY"]
    assert engine.llm_director.model == "gpt-5.4-mini" and engine.llm_director.reasoning_effort == "none"
