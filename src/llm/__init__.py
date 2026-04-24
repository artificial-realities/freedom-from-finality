from src.llm.base import LLMBackend
from src.llm.gemma import GemmaBackend
from src.llm.openai_backend import OpenAIBackend

__all__ = ["GemmaBackend", "LLMBackend", "OpenAIBackend", "build_backend"]


def build_backend(name: str, model: str) -> LLMBackend:
    """Construct an LLMBackend from a config's `llm_backend` string."""
    key = name.lower()
    if key == "gemma":
        return GemmaBackend(model=model)
    if key in {"openai", "gpt-4o", "gpt"}:
        return OpenAIBackend(model=model)
    raise ValueError(f"Unknown llm_backend: {name!r}")
