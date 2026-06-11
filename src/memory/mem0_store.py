"""Mem0-backed episodic story memory (plan 02).

Replaces Spock's full-replacement `narrative_memory` rewrite (bottleneck
B3) and the 5-turn-window dependency (B4): each turn's beat is added to
an embedded Mem0 store post-turn, and agents retrieve *relevant* past
events per turn instead of reading one rolling prose blob.

Everything runs in-process: embedded Qdrant under `logs/memory/`,
SQLite history alongside, OpenAI for extraction + embeddings. The
structured `world_state` ledger stays first-party — this store is the
episodic layer only.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _slug(value: str) -> str:
    """Mem0 entity ids reject whitespace — slugify story titles etc."""
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower() or "story"

# Mem0's default extraction is conversation-assistant biased: it date-stamps
# facts and silently drops story beats it deems "not memorable" (both observed
# in the plan-02 spike). The `prompt` argument to `add()` is NOT a replacement
# prompt — it is injected as a "## Custom Instructions" (highest-priority
# rules) section inside Mem0's own additive-extraction template, so this must
# be guidance only: no output-format contract, or extraction breaks.
NARRATIVE_EXTRACTION_INSTRUCTIONS = """\
This input is a turn from an ongoing visual story, NOT a user conversation.
Extract the concrete, durable story facts a future storyteller needs for
callbacks:
- events that happened; props introduced, used, or lost (keep prop names verbatim)
- running gags or bits; promises and setups
- changes to a character's physical state, abilities, or position
Every turn matters: if the input describes any event at all, extract at least
one memory. Phrase each memory as a short past-tense sentence about the story
world. Do not include dates or times. Never refer to "the user" or "the
assistant"."""


class StoryMemory:
    """Per-session episodic memory over an embedded Mem0 instance."""

    def __init__(
        self,
        *,
        model: str,
        session_id: str,
        story_id: str,
        data_dir: Path | str = "logs/memory",
    ) -> None:
        from mem0 import AsyncMemory
        from mem0.configs.base import MemoryConfig

        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        # Pin the extraction LLM explicitly — Mem0's default model rejects
        # `max_tokens` (newer OpenAI models want max_completion_tokens).
        config = MemoryConfig(**{
            "llm": {"provider": "openai", "config": {"model": model}},
            "vector_store": {
                "provider": "qdrant",
                "config": {"path": str(data_dir / "qdrant")},
            },
            "history_db_path": str(data_dir / "history.db"),
        })
        self._memory = AsyncMemory(config)
        self.session_id = _slug(session_id)
        self.story_id = _slug(story_id)

    async def add_turn(
        self,
        *,
        turn: int,
        narration: str,
        outcome: str = "",
        voiceover: str = "",
    ) -> int:
        """Store one turn's events. Returns the number of memories extracted."""
        parts = [narration]
        if outcome:
            parts.append(f"Outcome: {outcome}")
        if voiceover:
            parts.append(f"Voiceover noted: {voiceover}")
        text = "\n".join(parts)

        try:
            result = await self._memory.add(
                text,
                user_id=self.story_id,
                run_id=self.session_id,
                metadata={"turn": turn},
                prompt=NARRATIVE_EXTRACTION_INSTRUCTIONS,
            )
        except Exception:
            logger.exception("Mem0 add failed on turn %s — memory not stored", turn)
            return 0

        extracted = result.get("results", []) if isinstance(result, dict) else []
        if not extracted:
            logger.warning("Mem0 extracted zero memories on turn %s", turn)
        return len(extracted)

    async def search_block(self, query: str, *, limit: int = 6) -> str:
        """Retrieve relevant memories formatted as a prompt-ready block."""
        if not query.strip():
            return "(no query — no memories retrieved)"
        try:
            res = await self._memory.search(
                query,
                filters={"user_id": self.story_id, "run_id": self.session_id},
                top_k=limit,
            )
        except Exception:
            logger.exception("Mem0 search failed for query %r", query[:80])
            return "(memory retrieval failed this turn)"

        items = res.get("results", []) if isinstance(res, dict) else (res or [])
        if not items:
            return "(no relevant memories yet — early in the story)"
        lines = []
        for r in items:
            turn = (r.get("metadata") or {}).get("turn")
            suffix = f" (turn {turn})" if turn is not None else ""
            lines.append(f"- {r.get('memory')}{suffix}")
        return "\n".join(lines)
