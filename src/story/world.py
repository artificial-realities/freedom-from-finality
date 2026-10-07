"""Typed world model: the deterministic system of record for a run.

Everything the film has established lives here in structure, not prose:
entities (characters, props, locations) with status and whereabouts,
each character's posture, activity and position in the frame, open
threads, viewer interventions with their disposition, the plan (arc +
current scene card), an append-only event log (one record per clip),
and the summaries the Showrunner compacts the log into.

Only validated deltas mutate it (see `validator.py`). It is persisted
after every clip to `logs/sessions/<session>/` so a run can be
inspected (resume is not built: every run starts a fresh world).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from src.models.story import Perspective, Story, Voice

EntityKind = Literal["character", "prop", "location"]

# Durable notes kept per character (the Showrunner's `entity_notes`); the
# oldest goes when a new one arrives past the cap. Every kept note is shown.
MAX_NOTES = 4


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "entity"


def names(text: str, name: str) -> bool:
    """Whether `text` names `name` as a whole phrase, ignoring case."""
    return bool(name.strip()) and re.search(rf"\b{re.escape(name.strip().lower())}\b", text.lower()) is not None


class Entity(BaseModel):
    id: str
    name: str
    kind: EntityKind
    description: str = ""        # who/what it is (for the writer): a character's `who`
    look: str = ""               # how it looks (for the renderer; see `render_look`)
    look_short: str = ""         # a few words of look for clips that continue the picture
    status: str = ""             # props: short free text, "dented", "balanced on his head"
    location: str = ""           # location id
    holder: str = ""             # character id, "" when not held
    present: bool = False        # in the current scene
    gone: bool = False           # removed from the story world
    first_seen: int = 0
    last_seen: int = 0
    notes: list[str] = Field(default_factory=list)   # durable facts (Showrunner; characters only, at most MAX_NOTES)
    # Characters: how they sound, what they want and how they are (from the
    # blueprint); supporting characters' entrance conditions; `dynamic` =
    # introduced by the film, not written in the blueprint.
    voice: Voice | None = None
    wants: str = ""
    nature: str = ""
    appears: str = ""            # must | should | may (supporting characters of the blueprint)
    appears_when: str = ""
    dynamic: bool = False
    # Characters: the state the next clip continues from.
    posture: str = ""            # how the body is arranged: "standing", "lying on his back"
    activity: str = ""           # what they are doing, sustained: "asleep", "watching the ball"
    position: str = ""           # where in the frame: "centre frame", "off screen right"

    def render_look(self) -> str:
        """How the renderer is told it looks: its look, else (a prop) its
        description; a character's description says who it is, not how it
        looks, so a character without a look is named only."""
        if self.look:
            return self.look
        return self.name if self.kind == "character" else (self.description or self.name)

    def now(self) -> str:
        """A character's current state in one clause, or empty."""
        return "; ".join(x for x in (self.posture, self.activity, self.position) if x)

    def line(self) -> str:
        bits = [f"{self.id} ({self.kind}): {self.name}"]
        if self.kind == "character" and self.now():
            bits.append(f"now: {self.now()}")
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
    """One clip of the film as it was emitted: which beat it belongs to,
    what the camera saw, what changed."""

    segment: int
    clip_id: int = 0             # monotonic per run, never reused (0 = legacy log)
    scene_id: int
    beat: int = 0
    beat_intent: str = ""
    clip_index: int = 1          # 1-based position within the beat
    clip_count: int = 1
    user_input: str = ""
    intervention: str = "none"
    narration: str
    applied_ops: list[str] = Field(default_factory=list)
    thread_ops: list[str] = Field(default_factory=list)
    on_screen: list[str] = Field(default_factory=list)
    end_state: str = ""
    dialogue: list[str] = Field(default_factory=list)   # "Name: line", as spoken in the clip
    duration: float = 0.0        # delivered seconds of the clip's shape (older logs: whole seconds)


class ClipRecord(BaseModel):
    """The full record of one clip as it was planned, compiled and fired.
    Appended to clips.jsonl in the session directory; a rollback appends a
    dropped marker instead of rewriting. Replay and offline analysis read
    this; nothing reads it during a run."""

    clip_id: int
    beat_id: int
    parent_clip_id: int = 0      # 0 = seeded from the reference (the first clip)
    segment: int
    beat_intent: str = ""
    framing: str = ""
    intervention: str = "none"
    intervention_reason: str = ""
    perspective: str = ""
    dialogue: list[str] = Field(default_factory=list)
    action: str = ""
    end_state: str = ""
    camera_motion: str = "static"
    on_screen: list[str] = Field(default_factory=list)
    prompt: str = ""
    shape_frames: int = 0
    shape_seconds: float = 0.0
    seed: int | None = None
    viewer_line_ids: list[int] = Field(default_factory=list)
    world_hash: str = ""
    dropped: bool = False
    drop_reason: str = ""


class WorldModel(BaseModel):
    title: str = ""
    segment: int = 0
    protagonist: str = ""
    newcomers: str = ""          # what kind of characters the film may introduce; "" = none
    perspective: Perspective = Field(default_factory=Perspective)
    # The characters the reference image shows (ids): the picture before the
    # first clip. None = no reference, so nothing is said to enter on it.
    opening_picture: list[str] | None = None
    entities: dict[str, Entity] = Field(default_factory=dict)
    threads: list[Thread] = Field(default_factory=list)
    interventions: list[Intervention] = Field(default_factory=list)
    plan: Plan = Field(default_factory=Plan)
    act_summary: str = ""
    summaries: list[Summary] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    beats: int = 0               # beats planned so far
    last_compacted: int = 0
    last_change: int = 0
    last_showrunner: int = 0
    scene_started: int = 1
    showrunner_runs: int = 0
    viewer_momentum: str = ""    # the viewer's last absorbed input; persists across beats
    viewer_momentum_segment: int = 0
    shot: str = ""               # the shot the picture is in: the last beat's framing or a clip's cut (LTX-15)

    _saved_events: int = PrivateAttr(default=0)
    _clip_counter: int = PrivateAttr(default=0)
    _last_clip_id: int = PrivateAttr(default=0)
    _clip_records: list[ClipRecord] = PrivateAttr(default_factory=list)
    _saved_clips: int = PrivateAttr(default=0)

    # ── Construction ────────────────────────────────────────────────

    @classmethod
    def from_story(cls, story: Story) -> "WorldModel":
        """A fresh world from a blueprint."""
        ref = story.picture.reference
        return cls.from_blueprint(
            title=story.title, characters=story.cast.characters, locations=story.locations,
            protagonist=story.cast.protagonist, newcomers=story.cast.newcomers,
            perspective=story.picture.perspective, shows=ref.shows if ref is not None else None,
        )

    @classmethod
    def from_blueprint(
        cls, *, title: str, characters, locations, protagonist: str = "", newcomers: str = "",
        perspective: Perspective | None = None, shows: str | None = None,
    ) -> "WorldModel":
        """The world at the start of a film: every location, and every
        character of the blueprint with who it is, its look, voice and
        starting state. Only the protagonist (`protagonist` by name, or the
        first character) is in the scene; supporting characters wait until
        the film brings them in. `shows` is what the reference image shows
        (None: there is none); the characters it names are in the picture
        before the first clip."""
        world = cls(title=title, newcomers=newcomers.strip(),
                    perspective=(perspective or Perspective()).model_copy())
        for i, loc in enumerate(locations):
            lid = world._unique_id(slugify(loc.name))
            world.entities[lid] = Entity(
                id=lid, name=loc.name, kind="location",
                description=loc.description, present=(i == 0),
            )
        home = next((e.id for e in world.entities.values() if e.kind == "location"), "")
        lead = next((ch for ch in characters if protagonist and ch.name == protagonist), None) or (
            characters[0] if characters else None)
        for ch in characters:
            cid = world._unique_id(slugify(ch.name))
            is_lead = ch is lead
            world.entities[cid] = Entity(
                id=cid, name=ch.name, kind="character",
                description=ch.who, look=ch.look, look_short=ch.look_short,
                voice=ch.voice, wants=ch.wants, nature=ch.nature,
                appears="" if is_lead else ch.appears,
                appears_when="" if is_lead else ch.appears_when,
                posture=ch.posture, activity=ch.activity, position=ch.position,
                present=is_lead, location=home if is_lead else "",
            )
            if is_lead:
                world.protagonist = cid
        if shows is not None:
            world.opening_picture = [e.id for e in world.by_kind("character") if names(shows, e.name)]
        return world

    @property
    def cast_locked(self) -> bool:
        """No character may be introduced beyond the blueprint's cast."""
        return not self.newcomers

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

    def drop_pending_interventions(self, segment: int) -> int:
        """Forget pending lines recorded at `segment` (a cancelled call)."""
        before = len(self.interventions)
        self.interventions = [
            i for i in self.interventions
            if not (i.segment == segment and i.disposition == "pending")
        ]
        return before - len(self.interventions)

    def new_thread_id(self) -> str:
        return f"t{len(self.threads) + 1}"

    # ── Clocks, in story seconds ────────────────────────────────────

    def scene_length(self) -> int:
        """Clips in the current scene."""
        return max(0, self.segment - self.scene_started + 1)

    def scene_seconds(self) -> float:
        return float(sum(e.duration for e in self.events if e.segment >= self.scene_started))

    def stagnation(self) -> int:
        """Clips since anything in the world changed."""
        return max(0, self.segment - self.last_change)

    def seconds_since_change(self) -> float:
        return float(sum(e.duration for e in self.events if e.segment > self.last_change))

    def seconds_since(self, segment: int) -> float:
        """Story seconds played from `segment` on (inclusive): a thread's age."""
        return float(sum(e.duration for e in self.events if e.segment >= segment))

    def next_clip_id(self) -> int:
        """A new clip id: monotonic per run, never reused."""
        self._clip_counter += 1
        self._last_clip_id = self._clip_counter
        return self._clip_counter

    @property
    def last_clip_id(self) -> int:
        return self._last_clip_id

    @last_clip_id.setter
    def last_clip_id(self, value: int) -> None:
        self._last_clip_id = value

    def record_clip(self, record: ClipRecord) -> None:
        self._clip_records.append(record)

    def record_drop(self, clip_id: int, reason: str) -> None:
        self._clip_records.append(ClipRecord(
            clip_id=clip_id, beat_id=0, segment=0, dropped=True, drop_reason=reason,
        ))

    def events_since(self, segment: int) -> list[EventRecord]:
        return [e for e in self.events if e.segment > segment]

    def held_props(self, character_id: str | None = None) -> list[Entity]:
        cid = character_id or self.protagonist
        return [e for e in self.by_kind("prop") if e.holder == cid and not e.gone]

    # ── Persistence ─────────────────────────────────────────────────

    def save(self, directory: Path | str, *, rewrite: bool = False) -> None:
        """Write `world.json`, append new events to `events.jsonl` and new
        clip records to `clips.jsonl`; `rewrite` writes the whole event log
        again (after a rollback took a recorded clip back out)."""
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
        new_clips = self._clip_records[self._saved_clips:]
        if new_clips:
            with (directory / "clips.jsonl").open("a", encoding="utf-8") as f:
                for cr in new_clips:
                    f.write(cr.model_dump_json() + "\n")
            self._saved_clips = len(self._clip_records)

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
