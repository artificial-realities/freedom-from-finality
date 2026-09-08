"""Typed world model: the deterministic system of record for a run.

Everything the film has established lives here in structure, not prose:
entities (characters, props, locations) with status and whereabouts,
open threads, viewer interventions with their disposition, the plan
(arc + current scene card), an append-only event log, and the
summaries the Showrunner compacts the log into.

Only validated deltas mutate it (see `validator.py`). It is persisted
after every segment to `logs/sessions/<session>/` so a run can be
inspected or resumed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from src.story.schemas import Observation

EntityKind = Literal["character", "prop", "location"]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "entity"


class Entity(BaseModel):
    id: str
    name: str
    kind: EntityKind
    description: str = ""        # who/what it is (for the writer)
    look: str = ""               # how it looks (for the renderer); falls back to description
    status: str = ""             # short free text: "dented", "balanced on his head"
    location: str = ""           # location id
    holder: str = ""             # character id, "" when not held
    present: bool = False        # in the current scene
    gone: bool = False           # removed from the story world
    first_seen: int = 0
    last_seen: int = 0
    notes: list[str] = Field(default_factory=list)   # durable facts (Showrunner)
    speaks: bool = True
    reference_image: str = ""
    # Visual memory from frame observation: what the renderer last showed.
    observed_look: str = ""
    observed_at: int = 0
    drift: int = 0

    def render_look(self) -> str:
        return self.look or self.description or self.name

    def line(self) -> str:
        bits = [f"{self.id} ({self.kind}): {self.name}"]
        if self.status:
            bits.append(f"status: {self.status}")
        if self.holder:
            bits.append(f"held by {self.holder}")
        elif self.location and self.kind != "location":
            bits.append(f"at {self.location}")
        return " · ".join(bits)


class Thread(BaseModel):
    id: str
    text: str
    kind: Literal["setup", "promise", "gag", "tension"] = "setup"
    status: Literal["open", "paid_off", "dropped"] = "open"
    opened_at: int
    closed_at: int | None = None


class Intervention(BaseModel):
    segment: int
    text: str
    disposition: Literal["pending", "absorbed", "deferred", "declined"] = "pending"
    reason: str = ""
    resolved_at: int | None = None


class SceneCard(BaseModel):
    id: int
    goal: str
    tension: str
    exit_condition: str
    intensity: Literal["low", "medium", "high"] = "medium"
    candidate_beats: list[str] = Field(default_factory=list)
    opened_at: int


class Plan(BaseModel):
    arc: list[str] = Field(default_factory=list)
    current_act: int = 0
    scene: SceneCard | None = None


class Summary(BaseModel):
    scope: Literal["scene", "act"]
    first_segment: int
    last_segment: int
    text: str


class EventRecord(BaseModel):
    segment: int
    scene_id: int
    user_input: str = ""
    intervention: str = "none"
    narration: str
    outcome: str
    next_direction: str = ""
    applied_ops: list[str] = Field(default_factory=list)
    thread_ops: list[str] = Field(default_factory=list)
    on_screen: list[str] = Field(default_factory=list)
    transition: str = "continue"
    end_state: str = ""
    voiceover: str = ""
    duration: int = 0


class WorldModel(BaseModel):
    title: str = ""
    segment: int = 0
    protagonist: str = ""
    cast_locked: bool = False
    entities: dict[str, Entity] = Field(default_factory=dict)
    threads: list[Thread] = Field(default_factory=list)
    interventions: list[Intervention] = Field(default_factory=list)
    plan: Plan = Field(default_factory=Plan)
    act_summary: str = ""
    summaries: list[Summary] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    last_compacted: int = 0
    last_change: int = 0
    last_showrunner: int = 0
    scene_started: int = 1
    showrunner_runs: int = 0
    # Frame observation (visual memory): every observation kept, the latest
    # shown to the Director, and the last segment that opened with a cut.
    observations: list[Observation] = Field(default_factory=list)
    picture: Observation | None = None
    last_cut: int = 0

    _saved_events: int = PrivateAttr(default=0)

    # ── Construction ────────────────────────────────────────────────

    @classmethod
    def from_blueprint(cls, *, title: str, characters, locations, cast_locked: bool) -> "WorldModel":
        world = cls(title=title, cast_locked=cast_locked)
        for i, loc in enumerate(locations):
            lid = world._unique_id(slugify(loc.name))
            world.entities[lid] = Entity(
                id=lid, name=loc.name, kind="location",
                description=loc.description, present=(i == 0),
            )
        home = next((e.id for e in world.entities.values() if e.kind == "location"), "")
        for i, ch in enumerate(characters):
            cid = world._unique_id(slugify(ch.name))
            world.entities[cid] = Entity(
                id=cid, name=ch.name, kind="character",
                description=ch.description,
                look=getattr(ch, "look", "") or "",
                speaks=getattr(ch, "speaks", True),
                reference_image=getattr(ch, "reference_image", "") or "",
                present=(i == 0), location=home if i == 0 else "",
            )
            if i == 0:
                world.protagonist = cid
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
        hero = self.entities.get(self.protagonist)
        loc = self.resolve(hero.location) if hero and hero.location else None
        if loc is not None:
            return loc
        return next(iter(self.by_kind("location")), None)

    def open_threads(self) -> list[Thread]:
        return [t for t in self.threads if t.status == "open"]

    def unresolved_interventions(self) -> list[Intervention]:
        return [i for i in self.interventions if i.disposition in ("pending", "deferred")]

    def add_intervention(self, segment: int, text: str) -> Intervention:
        item = Intervention(segment=segment, text=text)
        self.interventions.append(item)
        return item

    def new_thread_id(self) -> str:
        return f"t{len(self.threads) + 1}"

    def scene_length(self) -> int:
        return max(0, self.segment - self.scene_started + 1)

    def stagnation(self) -> int:
        return max(0, self.segment - self.last_change)

    def events_since(self, segment: int) -> list[EventRecord]:
        return [e for e in self.events if e.segment > segment]

    def land_observation(self, obs: Observation) -> list[str]:
        """Record an observation as visual memory. Returns log lines."""
        notes: list[str] = []
        self.observations.append(obs)
        if self.picture is None or obs.segment >= self.picture.segment:
            self.picture = obs
        for check in obs.entities:
            ent = self.resolve(check.entity)
            if ent is None:
                continue
            ent.observed_at = obs.segment
            ent.drift = obs.drift if not check.matches else min(obs.drift, 1)
            ent.observed_look = (
                check.deviations.strip() if check.deviations.strip()
                else ("matches the sheet" if check.visible else "not visible")
            )
            notes.append(f"{ent.id}: {ent.observed_look}")
        if not obs.background_ok and obs.background_note.strip():
            notes.append(f"background: {obs.background_note.strip()}")
        return notes

    def held_props(self, character_id: str | None = None) -> list[Entity]:
        cid = character_id or self.protagonist
        return [e for e in self.by_kind("prop") if e.holder == cid and not e.gone]

    # ── Persistence ─────────────────────────────────────────────────

    def save(self, directory: Path | str) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "world.json").write_text(
            self.model_dump_json(indent=2, exclude={"events"}), encoding="utf-8",
        )
        new = self.events[self._saved_events:]
        if new:
            with (directory / "events.jsonl").open("a", encoding="utf-8") as f:
                for ev in new:
                    f.write(ev.model_dump_json() + "\n")
            self._saved_events = len(self.events)

    @classmethod
    def load(cls, directory: Path | str) -> "WorldModel":
        directory = Path(directory)
        world = cls.model_validate_json((directory / "world.json").read_text(encoding="utf-8"))
        events_path = directory / "events.jsonl"
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    world.events.append(EventRecord.model_validate_json(line))
        world._saved_events = len(world.events)
        return world
