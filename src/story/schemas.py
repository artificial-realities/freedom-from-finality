"""Structured outputs for the Director and Showrunner calls.

Both schemas are consumed through OpenAI strict structured outputs, so
they stay inside the strict subset: no free-form dicts, every field a
plain type, list or nested model. Validation against the world happens
in `src/story/validator.py`, not here.

The Director writes a **beat**: one action or state of the film that
lasts a few clips. Each clip carries its own picture and the world
changes that happen in it, so a beat can be cut short by the viewer
after any clip and the world stays true to what was rendered.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityKind = Literal["character", "prop", "location"]


class EntityOp(BaseModel):
    """One world change proposed by the Director, in the clip where it
    happens.

    `entity` is an existing entity id for `update`/`remove`, or the name
    of a new thing for `introduce`. Empty strings mean "unchanged".
    `description` (introduce only) is how the new thing looks; a new
    character also says `who` it is in the story and how its `voice`
    sounds, so it is registered once and stays the same.
    `holder` takes a character id, or the literal `nobody`. `posture`,
    `activity` and `position` are a character's state at the end of the
    clip; `status` is a prop's.
    """

    op: Literal["introduce", "update", "remove"]
    entity: str
    kind: EntityKind = "prop"
    description: str = ""
    who: str = ""
    voice: str = ""
    status: str = ""
    location: str = ""
    holder: str = ""
    present: bool | None = None
    posture: str = ""
    activity: str = ""
    position: str = ""


class ThreadOp(BaseModel):
    """Open a setup/promise/gag, or pay off / drop an existing one by id."""

    op: Literal["open", "pay_off", "drop"]
    thread: str
    kind: Literal["setup", "promise", "gag", "tension"] = "setup"


class DialogueLine(BaseModel):
    speaker: str
    line: str
    delivery: str = ""


class ClipSpec(BaseModel):
    """One clip of a beat: the next few seconds of continuous picture.
    `action` is the only prose the film has; the transcript, the player
    and the Showrunner's log all read it.

    `cut_to` (LTX-15, only where the story allows cuts; "" = the clip
    continues the shot): the shot the clip cuts to, at its start; the
    clip's action, end state and camera move then belong to that shot,
    which holds after the clip until a camera move or another cut. It
    comes first so the Director decides the shot before writing in it."""

    cut_to: str = ""
    action: str
    end_state: str
    camera_motion: str = "static"
    ambient_sound: str = ""
    on_screen: list[str] = Field(default_factory=list)
    dialogue: list[DialogueLine] = Field(default_factory=list)
    entity_ops: list[EntityOp] = Field(default_factory=list)

    def action_text(self) -> str:
        text = " ".join(self.action.split())
        if text and text[-1] not in ".!?":
            text += "."
        cut = " ".join(self.cut_to.split()).rstrip(".")
        if cut:
            text = f"Cut to {cut}. {text}".strip()
        return text


class BeatPlan(BaseModel):
    """The Director's whole output: one beat, one to a few clips."""

    intent: str
    framing: str
    clips: list[ClipSpec]
    thread_ops: list[ThreadOp] = Field(default_factory=list)
    intervention: Literal["none", "absorbed", "deferred", "declined"] = "none"
    intervention_reason: str = ""
    # The perspective this beat is shot from when it switches away from the
    # story's default ("" = the default); the story says which are allowed.
    perspective: str = ""
    # The segment of the earlier, deferred request this beat delivers (as
    # listed under "Earlier requests still owed"); 0 = none.
    delivered_request: int = 0
    scene_done: bool = False
    needs_replan: bool = False


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
