from src.llm.base import LLMBackend
from src.llm.openai_backend import OpenAIBackend

__all__ = ["LLMBackend", "OpenAIBackend", "build_backend"]


def build_backend(name: str, model: str, *, service_tier: str = "", reasoning_effort: str = "") -> LLMBackend:
    """Construct an LLMBackend from a config's `llm_backend` string.
    `service_tier` is an OpenAI processing tier ("priority" for faster
    tokens at a higher price); `reasoning_effort` marks an OpenAI reasoning
    model and sets its effort ("none" for the fastest). Other backends
    ignore both."""
    key = name.lower()
    if key in {"openai", "gpt-4o", "gpt-4.1", "gpt"}:
        return OpenAIBackend(model=model, service_tier=service_tier, reasoning_effort=reasoning_effort)
    if key == "mock":
        from src.llm.mock_backend import MockBackend
        return MockBackend(model=model)
    raise ValueError(f"Unknown llm_backend: {name!r}")
