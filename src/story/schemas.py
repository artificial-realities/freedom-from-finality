"""Structured outputs for the Now and Then calls.

Both schemas are consumed through OpenAI strict structured outputs, so
they stay inside the strict subset: no free-form dicts, every field a
plain type, list or nested model. Validation against the world happens
in `src/story/validator.py`, not here.

Now writes a **beat**: one action or state of the story that lasts a
few clips. Each clip carries its own picture and the world changes that
happen in it, so a beat can be cut short by the viewer after any clip
and the world stays true to what was rendered.

Then writes a **notebook update**: compressed memory, the rewritten
notebook (characters, seeds, intentions, audience), and optionally an
era recognised retrospectively.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityKind = Literal["character", "prop", "location"]


class EntityOp(BaseModel):
    """One world change proposed by Now, in the clip where it happens.

    `entity` is an existing entity id for `update`/`remove`, or the name
    of a new thing for `introduce`. Empty strings mean "unchanged".
    `description` is how a new thing looks (introduce), or the whole new
    look of an existing character, prop or place whose appearance changes
    for good (update); a new character also gives its `personality`, as
    the frame zero gives its character's, and how its `voice` sounds, so
    it is registered once and stays the same. An update may give a
    character the voice it lacks, or a new one; personality is Then's to
    rewrite. `state` is the entity at the end of the clip, in prose,
    replacing its last one.
    """

    op: Literal["introduce", "update", "remove"]
    entity: str
    kind: EntityKind = "prop"
    description: str = ""
    personality: str = ""
    voice: str = ""
    state: str = ""
    location: str = ""
    present: bool | None = None


class DialogueLine(BaseModel):
    speaker: str
    line: str
    delivery: str = ""


class ClipSpec(BaseModel):
    """One clip of a beat: the next few seconds of continuous picture.
    `action` is the only prose the story has; the transcript, the player
    and Then's log all read it.

    `cut_to` (only where the story allows cuts; "" = the clip
    continues the shot): the shot the clip cuts to, at its start; the
    clip's action, end state and camera move then belong to that shot,
    which holds after the clip until a camera move or another cut."""

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
    """Now's whole output: one beat, one to a few clips."""

    intent: str
    framing: str
    clips: list[ClipSpec]
    intervention: Literal["none", "absorbed", "deferred", "declined"] = "none"
    intervention_reason: str = ""
    delivered_request: int = 0


# ── Then schemas ────────────────────────────────────────────────────

class CharacterPortrait(BaseModel):
    """`personality` is who the character is at heart, and changes slowly:
    empty keeps the one it has, so it is not reworded on every run, and a
    new one needs `because`, what in the story changed it. `lately` is how
    it has been behaving and why, rewritten each run."""

    entity: str
    personality: str
    because: str
    lately: str


class Seed(BaseModel):
    seed: str
    potential: str


class Notebook(BaseModel):
    characters: list[CharacterPortrait] = Field(default_factory=list)
    alive: str = ""
    seeds: list[Seed] = Field(default_factory=list)
    intentions: str = ""
    audience: str = ""


class EraOut(BaseModel):
    name: str
    turned_at: int
    chronicle: str


class ThenUpdate(BaseModel):
    """Then's output: memory compression plus the rewritten notebook."""

    scene_summary: str = ""
    act_summary: str = ""
    notebook: Notebook
    era: EraOut | None = None
    rationale: str = ""
