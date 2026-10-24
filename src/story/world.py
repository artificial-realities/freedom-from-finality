"""Typed world model: the deterministic system of record for a run.

Everything the story has established lives here: entities (characters,
props, locations) with what code needs in structure (kind, presence,
location, whether they are gone) and their state in one line of prose,
viewer interventions with their disposition, Then's notebook, an
append-only event log (one record per clip), and the summaries Then
compacts the log into.

Only validated deltas mutate it (see `validator.py`). It is persisted
after every clip to `logs/sessions/<session>/` so a run can be
inspected (resume is not built: every run starts a fresh world).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from src.models.story import FrameZero, Voice
from src.story.schemas import Notebook

EntityKind = Literal["character", "prop", "location"]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", re.sub(r"['']", "", name.strip().lower())).strip("_")
    return slug or "entity"


SHORT_LOOK_WORDS = 14


def short_look(look: str) -> str:
    """The look restated on every clip: its first phrase, at most
    SHORT_LOOK_WORDS words, ending on a whole phrase."""
    text = " ".join(look.split()).rstrip(".")
    for stop in ("; ", ". "):
        cut = text.find(stop)
        if cut > 0:
            text = text[:cut]
    words = text.split()
    if len(words) > SHORT_LOOK_WORDS:
        text = " ".join(words[:SHORT_LOOK_WORDS])
        comma = text.rfind(",")
        joint = text.rfind(" and ")
        if comma > 0:
            text = text[:comma]
        elif joint > len(text) // 2:
            text = text[:joint]
    return text.rstrip(" ,;:")


class Entity(BaseModel):
    """`description` is how a prop or place looks as introduced, or, for a
    character, who it is: the frame zero's personality or the `who` Now
    gave it, shown to the roles until Then's notebook has a portrait of it."""

    id: str
    name: str
    kind: EntityKind
    description: str = ""
    look: str = ""
    look_short: str = ""
    look_since: int = 0
    state: str = ""
    location: str = ""
    present: bool = False
    gone: bool = False
    first_seen: int = 0
    last_seen: int = 0
    voice: Voice | None = None
    dynamic: bool = False

    def render_look(self) -> str:
        if self.look:
            return self.look
        return self.name if self.kind == "character" else (self.description or self.name)

    def line(self) -> str:
        bits = [f"{self.id} ({self.kind}): {self.name}"]
        if self.state:
            bits.append(f"now: {self.state}")
        if self.location and self.kind != "location":
            bits.append(f"at {self.location}")
        return " · ".join(bits)


class Intervention(BaseModel):
    segment: int
    text: str
    disposition: Literal["pending", "absorbed", "deferred", "declined"] = "pending"
    reason: str = ""
    resolved_at: int | None = None


class Era(BaseModel):
    name: str
    turned_at: int
    chronicle: str


class Summary(BaseModel):
    scope: Literal["scene", "act"]
    first_segment: int
    last_segment: int
    text: str


class EventRecord(BaseModel):
    """One clip of the story as it was emitted."""

    segment: int
    clip_id: int = 0
    beat: int = 0
    beat_intent: str = ""
    clip_index: int = 1
    clip_count: int = 1
    user_input: str = ""
    intervention: str = "none"
    narration: str
    applied_ops: list[str] = Field(default_factory=list)
    on_screen: list[str] = Field(default_factory=list)
    end_state: str = ""
    dialogue: list[str] = Field(default_factory=list)
    location: str = ""
    duration: float = 0.0


class WorldModel(BaseModel):
    segment: int = 0
    opening_picture: list[str] | None = None
    camera_location: str = ""

    entities: dict[str, Entity] = Field(default_factory=dict)
    interventions: list[Intervention] = Field(default_factory=list)
    notebook: Notebook = Field(default_factory=Notebook)
    eras: list[Era] = Field(default_factory=list)
    act_summary: str = ""
    summaries: list[Summary] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)

    beats: int = 0
    last_compacted: int = 0
    last_then: int = 0
    then_runs: int = 0
    viewer_momentum: str = ""
    viewer_momentum_segment: int = 0
    shot: str = ""

    _saved_events: int = PrivateAttr(default=0)
    _clip_counter: int = PrivateAttr(default=0)
    _last_clip_id: int = PrivateAttr(default=0)

    # ── Construction ────────────────────────────────────────────────

    @classmethod
    def from_frame_zero(cls, frame_zero: FrameZero) -> "WorldModel":
        """A fresh world from a frame zero: its place and its character,
        ordinary entities from here on."""
        world = cls()
        place, ch = frame_zero.location, frame_zero.character
        lid = slugify(place.name)
        look = " ".join(place.look.split())
        world.entities[lid] = Entity(id=lid, name=place.name.strip(), kind="location",
                                     look=look, look_short=short_look(look), present=True)
        world.camera_location = lid
        cid = world._unique_id(slugify(ch.name))
        look = " ".join(ch.look.split())
        world.entities[cid] = Entity(
            id=cid, name=ch.name.strip(), kind="character",
            description=" ".join(ch.personality.split()), look=look, look_short=short_look(look),
            state=" ".join(ch.state.split()), present=True, location=lid,
        )
        # The first frame shows the character, so the opening clip does not
        # describe it as entering.
        world.opening_picture = [cid]
        return world

    def _unique_id(self, base: str) -> str:
        if base not in self.entities:
            return base
        n = 2
        while f"{base}_{n}" in self.entities:
            n += 1
        return f"{base}_{n}"

    # ── Lookup ──────────────────────────────────────────────────────

    def resolve(self, ref: str) -> Entity | None:
        if not ref:
            return None
        ref = ref.strip()
        if ref in self.entities:
            return self.entities[ref]
        sid = slugify(ref)
        if sid in self.entities:
            return self.entities[sid]
        low = ref.lower()
        for ent in self.entities.values():
            if ent.name.lower() == low:
                return ent
        return None

    def by_kind(self, kind: EntityKind) -> list[Entity]:
        return [e for e in self.entities.values() if e.kind == kind]

    def present_entities(self) -> list[Entity]:
        return [e for e in self.entities.values()
                if e.present and not e.gone and e.kind != "location"]

    def dormant_props(self) -> list[Entity]:
        return [e for e in self.by_kind("prop") if not e.present and not e.gone]

    def gone_props(self) -> list[Entity]:
        return [e for e in self.by_kind("prop") if e.gone]

    def current_location(self) -> Entity | None:
        if self.camera_location:
            loc = self.entities.get(self.camera_location)
            if loc is not None:
                return loc
        return next(iter(self.by_kind("location")), None)

    def unresolved_interventions(self) -> list[Intervention]:
        return [i for i in self.interventions if i.disposition in ("pending", "deferred")]

    def add_intervention(self, segment: int, text: str) -> Intervention:
        item = Intervention(segment=segment, text=text)
        self.interventions.append(item)
        return item

    def drop_pending_interventions(self, segment: int) -> int:
        before = len(self.interventions)
        self.interventions = [
            i for i in self.interventions
            if not (i.segment == segment and i.disposition == "pending")
        ]
        return before - len(self.interventions)

    # ── Clocks ──────────────────────────────────────────────────────

    def next_clip_id(self) -> int:
        self._clip_counter += 1
        self._last_clip_id = self._clip_counter
        return self._clip_counter

    @property
    def last_clip_id(self) -> int:
        return self._last_clip_id

    @last_clip_id.setter
    def last_clip_id(self, value: int) -> None:
        self._last_clip_id = value

    def events_since(self, segment: int) -> list[EventRecord]:
        return [e for e in self.events if e.segment > segment]

    # ── Persistence ─────────────────────────────────────────────────

    def save(self, directory: Path | str, *, rewrite: bool = False) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "world.json").write_text(
            self.model_dump_json(indent=2, exclude={"events"}), encoding="utf-8",
        )
        if rewrite:
            (directory / "events.jsonl").write_text(
                "".join(ev.model_dump_json() + "\n" for ev in self.events), encoding="utf-8")
            self._saved_events = len(self.events)
        else:
            new = self.events[self._saved_events:]
            if new:
                with (directory / "events.jsonl").open("a", encoding="utf-8") as f:
                    for ev in new:
                        f.write(ev.model_dump_json() + "\n")
                self._saved_events = len(self.events)
