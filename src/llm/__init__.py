from src.llm.base import LLMBackend
from src.llm.openai_backend import OpenAIBackend

__all__ = ["LLMBackend", "OpenAIBackend", "build_backend"]


def build_backend(name: str, model: str) -> LLMBackend:
    """Construct an LLMBackend from a config's `llm_backend` string."""
    key = name.lower()
    if key in {"openai", "gpt-4o", "gpt-4.1", "gpt"}:
        return OpenAIBackend(model=model)
    raise ValueError(f"Unknown llm_backend: {name!r}")
