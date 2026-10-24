"""Typed world model: the deterministic system of record for a run.

Everything the story has established lives here: entities (characters,
props, locations) with what code needs in structure (kind, presence,
location, whether they are gone) and their state in one line of prose,
viewer interventions with their disposition, Then's notebook, an
append-only event log (one record per clip), and the summaries Then
compacts the log into.

A world is one timeline (`timeline`). What the viewer said and put into
the story, and Then's understanding of them, belong to the person and
live outside it, in the viewer's memory (`viewer.py`).

Only validated deltas mutate it (see `validator.py`). The adventure
(`src/adventure.py`) saves it as of every clip the viewer sees and loads
it to resume; in memory it keeps only the recent events the prompts read
(`trim_events`), while the adventure's event log keeps them all.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from src.models.story import FrameZero, Voice
from src.story.schemas import Notebook

EntityKind = Literal["character", "prop", "location"]


ROOT_TIMELINE = "root"


def words(text: str) -> str:
    """Text as lowercase words between single spaces, padded, so a name
    can be found in it as whole words."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", re.sub(r"['']", "", name.strip().lower())).strip("_")
    return slug or "entity"


SHORT_LOOK_WORDS = 14
KEEP_EVENTS = 24      # events kept in memory: at least the last clips the prompts read
MOST_EVENTS = 200     # and at most this many, however long Then goes without compacting


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
    """`description` is how the entity looked when it was introduced.

    A character also carries who it is, on the entity so that it outlives
    the notebook: `personality` (who it is at heart: the one it came with,
    from the frame zero or Now's introduce, until Then rewrites it, at
    segment `personality_since` and `personality_because` of what the story
    showed) and `lately` (how it has been behaving and why, as Then last
    wrote it, at segment `lately_at`). A character Then drops from the
    notebook keeps both, so one that comes back much later is still who it
    was."""

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
    personality: str = ""
    personality_since: int = 0
    personality_because: str = ""
    lately: str = ""
    lately_at: int = 0

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
    """A viewer line as the story took it. `said` are the ids of the lines
    it joins in the viewer's memory."""

    segment: int
    text: str
    said: list[int] = Field(default_factory=list)
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


class Direction(BaseModel):
    """The viewer's line Now is still building on, from the clip that
    took it up until Now closes it or a newer line takes its place."""

    text: str
    segment: int
    said: list[int] = Field(default_factory=list)
    reason: str = ""


class WorldModel(BaseModel):
    timeline: str = ROOT_TIMELINE
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
    direction: Direction | None = None
    shot: str = ""
    # Clip ids are never reused, across sessions included: the renderer
    # keys the frames it continues from by them.
    clip_counter: int = 0
    last_clip_id: int = 0

    # ── Construction ────────────────────────────────────────────────

    @classmethod
    def from_frame_zero(cls, frame_zero: FrameZero) -> "WorldModel":
        """A fresh world from a frame zero: its place and its character,
        built as Now's introduce builds them, ordinary entities from here on."""
        world = cls()
        place, ch = frame_zero.location, frame_zero.character
        lid = slugify(place.name)
        look = " ".join(place.look.split())
        world.entities[lid] = Entity(id=lid, name=place.name.strip(), kind="location", description=look,
                                     look=look, look_short=short_look(look), present=True)
        world.camera_location = lid
        cid = world._unique_id(slugify(ch.name))
        look = " ".join(ch.look.split())
        world.entities[cid] = Entity(
            id=cid, name=ch.name.strip(), kind="character", description=look,
            look=look, look_short=short_look(look), personality=" ".join(ch.personality.split()),
            voice=Voice.coerce(" ".join(ch.voice.split())),
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

    def mentions(self, text: str) -> set[str]:
        """Ids of the entities `text` names, by name or id, as whole words."""
        said = words(text)
        return {e.id for e in self.entities.values() if any(words(n) in said for n in (e.name, e.id))}

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

    def add_intervention(self, segment: int, text: str, said: list[int] = ()) -> Intervention:
        item = Intervention(segment=segment, text=text, said=list(said))
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
        self.clip_counter += 1
        self.last_clip_id = self.clip_counter
        return self.clip_counter

    def events_since(self, segment: int) -> list[EventRecord]:
        return [e for e in self.events if e.segment > segment]

    def trim_events(self) -> None:
        """Keep in memory only the events the prompts read: those since Then
        last compacted (at most MOST_EVENTS) and at least the last
        KEEP_EVENTS. The story's whole history is the adventure's event log."""
        keep = max(KEEP_EVENTS, sum(1 for e in self.events if e.segment > self.last_compacted))
        del self.events[:-min(keep, MOST_EVENTS)]
