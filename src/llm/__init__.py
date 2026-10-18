from src.llm.base import LLMBackend
from src.llm.openai_backend import OpenAIBackend

__all__ = ["LLMBackend", "OpenAIBackend", "build_backend"]


def build_backend(name: str, model: str, *, reasoning_effort: str = "") -> LLMBackend:
    """The backend a config's `llm_backend` names: "openai", or "mock" (the
    scripted backend the offline tests run on)."""
    key = name.lower()
    if key == "openai":
        return OpenAIBackend(model=model, reasoning_effort=reasoning_effort)
    if key == "mock":
        from src.llm.mock_backend import MockBackend
        return MockBackend(model=model)
    raise ValueError(f"Unknown llm_backend: {name!r} (use \"openai\" or \"mock\")")
