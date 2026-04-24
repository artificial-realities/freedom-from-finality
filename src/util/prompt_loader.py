"""Load `.md` prompt templates with `{variable}` placeholders.

Cached per file path; substitution happens at call time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=64)
def _read_file(filepath: str) -> str:
    return Path(filepath).read_text(encoding="utf-8").strip()


def load_prompt(filepath: Path | str, **kwargs: Any) -> str:
    content = _read_file(str(filepath))
    return content.format(**kwargs) if kwargs else content


def prompt_path(name: str) -> Path:
    """Resolve a prompt filename (e.g. "narrator.system.md") to its path."""
    return PROMPTS_DIR / name
