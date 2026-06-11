"""Structured output schemas for every LLM call.

Every agent emits one of these. All schemas pass through the
json_sanitizer pipeline before instantiation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Beat(BaseModel):
    """Tolkien's output per turn — the richest downstream source."""

    narration: str
    action: str
    outcome: str
    short_term_narrative: str
    long_term_narrative: str | None = None


class Shot(BaseModel):
    """Spielberg's output per turn — the i2v prompt and its anchors."""

    i2v_prompt: str
    on_screen: list[str] = Field(default_factory=list)
    camera: str
    motion: str
    end_frame_description: str
    duration_seconds: int = Field(default=5, ge=3, le=15)


class Commentary(BaseModel):
    """Attenborough's output per turn — the spoken voiceover line.

    Empty `voiceover` means stay silent. The line's runtime is measured
    after TTS; the live producer concatenates as many silent clips behind
    it as the audio length warrants and holds Attenborough silent on
    those turns.
    """

    voiceover: str


class WorldStateDelta(BaseModel):
    """Spock's structured state update, merged into StoryState.world_state.

    Partial-update semantics:
    - `characters`: partial per-character dict merge.
    - `protagonist_location`: empty string means unchanged.
    - `inventory`: None means unchanged; a list means full replacement.
    """

    characters: dict[str, dict] = Field(default_factory=dict)
    protagonist_location: str = ""
    inventory: list[str] | None = None


class MemoryUpdate(BaseModel):
    """Spock's output per turn (prose memory mode)."""

    world_state_delta: WorldStateDelta = Field(default_factory=WorldStateDelta)
    narrative_memory: str
    context_brief: str


class MemoryUpdateLite(BaseModel):
    """Spock's output per turn in mem0 mode — no prose-memory rewrite.

    The episodic layer (narrative memory) lives in the Mem0 store; Spock
    keeps only the structured world ledger and the one-turn-delay brief.
    """

    world_state_delta: WorldStateDelta = Field(default_factory=WorldStateDelta)
    context_brief: str
