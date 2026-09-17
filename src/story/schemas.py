"""Structured outputs for the Director and Showrunner calls.

Both schemas are consumed through OpenAI strict structured outputs, so
they stay inside the strict subset: no free-form dicts, every field a
plain type, list or nested model. Validation against the world happens
in `src/story/validator.py`, not here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityKind = Literal["character", "prop", "location"]


class EntityOp(BaseModel):
    """One world change proposed by the Director.

    `entity` is an existing entity id for `update`/`remove`, or the name
    of a new thing for `introduce`. Empty strings mean "unchanged".
    `holder` takes a character id, or the literal `nobody`.
    """

    op: Literal["introduce", "update", "remove"]
    entity: str
    kind: EntityKind = "prop"
    description: str = ""
    status: str = ""
    location: str = ""
    holder: str = ""
    present: bool | None = None


class ThreadOp(BaseModel):
    """Open a setup/promise/gag, or pay off / drop an existing one by id."""

    op: Literal["open", "pay_off", "drop"]
    thread: str
    kind: Literal["setup", "promise", "gag", "tension"] = "setup"


class DialogueLine(BaseModel):
    speaker: str
    line: str
    delivery: str = ""


class ShotSpec(BaseModel):
    """The one continuous shot of a segment. The prompt compiler turns this
    into the video model's dialect; nothing here is model-specific."""

    framing: str
    camera_motion: str = "static"
    action_beats: list[str] = Field(default_factory=list)
    on_screen: list[str] = Field(default_factory=list)
    ambient_sound: str = ""
    end_state: str
    duration_seconds: int = Field(default=5, ge=3, le=15)


class Segment(BaseModel):
    """The Director's whole output for one segment of the film."""

    narration: str
    outcome: str
    next_direction: str
    shot: ShotSpec
    dialogue: list[DialogueLine] = Field(default_factory=list)
    voiceover: str = ""
    entity_ops: list[EntityOp] = Field(default_factory=list)
    thread_ops: list[ThreadOp] = Field(default_factory=list)
    intervention: Literal["none", "absorbed", "deferred", "declined"] = "none"
    intervention_reason: str = ""
    scene_done: bool = False
    needs_replan: bool = False

    def action_text(self) -> str:
        beats: list[str] = []
        for b in self.shot.action_beats:
            b = " ".join(b.split())
            if b:
                beats.append(b if b[-1] in ".!?" else b + ".")
        return " ".join(beats)


class EntityNote(BaseModel):
    entity: str
    note: str


class SceneCardOut(BaseModel):
    goal: str
    tension: str
    exit_condition: str
    intensity: Literal["low", "medium", "high"] = "medium"
    candidate_beats: list[str] = Field(default_factory=list)


class ShowrunnerUpdate(BaseModel):
    """The Showrunner's output: compaction plus plan."""

    scene_summary: str = ""
    act_summary: str = ""
    entity_notes: list[EntityNote] = Field(default_factory=list)
    threads_to_drop: list[str] = Field(default_factory=list)
    arc: list[str] = Field(default_factory=list)
    current_act: int = 0
    replan: bool = True
    next_scene: SceneCardOut
    rationale: str = ""
