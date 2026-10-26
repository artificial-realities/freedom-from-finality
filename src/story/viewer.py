"""The viewer's memory: what the person watching has said and put into
the story, and Then's understanding of them.

It belongs to the person, not to the world: the world is rolled back
when a clip never reaches the screen, and this memory never is. Every
record carries the clip it happened at. Nothing is edited or removed:
the memory is append-only, saved one record per line to `viewer.jsonl`,
each flushed to disk before the next, so a crash costs at most the record
being written, and that only damages its own line.

Facts and interpretation are kept apart. Code writes the facts: each
input as it arrives with the clip on air then (`Input`), and what the
story did with it once that clip reached the screen (`Outcome`: how a
beat took it, a deferred one later delivered or let go, a direction
ended). Then writes the interpretation: its understanding of the viewer,
each reading citing the inputs it rests on (`Understanding`), and the
viewer's marks on the story (`Mark`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PrivateAttr, TypeAdapter, ValidationError

from src.story.schemas import Reading, ThenUpdate, ViewerUnderstanding
from src.story.world import WorldModel, words
from src.util.durable import append_line, read_jsonl

logger = logging.getLogger(__name__)

# Then's readings of the viewer, as it is told: a few.
READINGS_KEPT = 6

# What became of an input: how the beat that answered it took it; a
# deferred one later delivered or let go; or, for the direction it became,
# how that ended.
OutcomeKind = Literal["absorbed", "deferred", "declined", "delivered", "let go",
                      "fulfilled", "ran its course", "superseded"]


class Input(BaseModel):
    """An input as the viewer typed it. `clip` is the clip on air then (0
    before the first), `during` what that clip showed, and `entities` who
    and what was in it or named by the input."""

    kind: Literal["input"] = "input"
    id: int
    clip: int
    text: str
    during: str = ""
    entities: list[str] = Field(default_factory=list)


class Outcome(BaseModel):
    """What the story did with inputs (`inputs`), recorded when the clip
    that did it reached the screen."""

    kind: Literal["outcome"] = "outcome"
    inputs: list[int]
    clip: int
    outcome: OutcomeKind
    reason: str = ""
    beat: str = ""


class Mark(BaseModel):
    """Something the viewer put into the story, as Then noticed it: a name,
    a decision, a relationship, a change. `clip` is where it entered the
    story; `entities` what it touches, for finding it again; `replaces`
    the mark it takes the place of, when the viewer changed their mind
    (that one stays in the memory, no longer shown)."""

    kind: Literal["mark"] = "mark"
    id: int
    clip: int
    mark: str
    inputs: list[int]
    entities: list[str] = Field(default_factory=list)
    replaces: int = 0


class Understanding(ViewerUnderstanding):
    """Then's understanding of the viewer, landed at `clip`. `seen` is how
    many records the memory held when Then was given it to read: whatever
    came after is new to its next run."""

    kind: Literal["understanding"] = "understanding"
    clip: int
    seen: int = 0


Record = Annotated[Input | Outcome | Mark | Understanding, Field(discriminator="kind")]
_RECORD = TypeAdapter(Record)


class ViewerMemory(BaseModel):
    records: list[Record] = Field(default_factory=list)
    _path: Path | None = PrivateAttr(default=None)

    @classmethod
    def at(cls, path: Path | None) -> "ViewerMemory":
        """An empty memory that appends every record to `path`."""
        memory = cls()
        memory._path = path
        return memory

    @classmethod
    def load(cls, path: Path) -> "ViewerMemory":
        """The memory saved at `path` (empty when there is none), appending to
        it from now on. A record a crash cut off is skipped."""
        memory = cls.at(path)
        for raw in read_jsonl(path):
            try:
                memory.records.append(_RECORD.validate_python(raw))
            except ValidationError:
                logger.warning("A record in %s is not a viewer record; skipped: %s", path, str(raw)[:200])
        return memory

    def add(self, record: Record) -> Record:
        self.records.append(record)
        if self._path is not None:
            try:
                append_line(self._path, record.model_dump_json())
            except OSError:
                logger.exception("Could not append to the viewer's memory at %s", self._path)
        return record

    # ── Views ───────────────────────────────────────────────────────

    def inputs(self) -> list[Input]:
        return [r for r in self.records if isinstance(r, Input)]

    def outcomes(self) -> list[Outcome]:
        return [r for r in self.records if isinstance(r, Outcome)]

    def all_marks(self) -> list[Mark]:
        return [r for r in self.records if isinstance(r, Mark)]

    def marks(self) -> list[Mark]:
        """The marks that stand: not replaced by a later one."""
        marks = self.all_marks()
        replaced = {m.replaces for m in marks if m.replaces}
        return [m for m in marks if m.id not in replaced]

    def understanding(self) -> Understanding | None:
        return next((r for r in reversed(self.records) if isinstance(r, Understanding)), None)

    def unanswered(self) -> list[Input]:
        """The inputs typed after the story last answered one that it never
        answered: what the viewer said just before the story stopped."""
        answered = {i for o in self.outcomes() for i in o.inputs}
        inputs = self.inputs()
        last = max((s.id for s in inputs if s.id in answered), default=0)
        return [s for s in inputs if s.id > last and s.id not in answered]

    # ── Writing ─────────────────────────────────────────────────────

    def hear(self, text: str, *, clip: int, during: str, entities: set[str]) -> Input:
        line = Input(id=max((s.id for s in self.inputs()), default=0) + 1, clip=clip,
                     text=" ".join(text.split()), during=during, entities=sorted(entities))
        return self.add(line)

    def land(self, update: ThenUpdate, world: WorldModel, *, at_clip: int, seen: int) -> list[str]:
        """Land Then's understanding of the viewer and its new marks. A
        reading must cite an input it rests on and a mark the inputs it came
        from: ids that name no input are dropped, and a reading or mark left
        citing none is not kept. A mark already recorded is not recorded
        again; one that replaces a standing mark retires it. A mark touches
        what it and its inputs name, and what the clip that first answered
        those inputs showed, when the world still holds that clip."""
        notes: list[str] = []
        inputs = {line.id: line for line in self.inputs()}
        readings: list[Reading] = []
        for r in update.viewer.readings:
            text = " ".join(r.reading.split())
            grounds = sorted({g for g in r.grounds if g in inputs})
            if text and grounds:
                readings.append(Reading(reading=text, grounds=grounds))
            elif text:
                notes.append(f"viewer reading not kept, it cites no input: {text}")
        if len(readings) > READINGS_KEPT:
            notes.append(f"{len(readings)} readings; the first {READINGS_KEPT} kept")
            del readings[READINGS_KEPT:]
        self.add(Understanding(readings=readings, lately=" ".join(update.viewer.lately.split()),
                               unknown=" ".join(update.viewer.unknown.split()), clip=at_clip, seen=seen))
        notes.append(f"understanding of the viewer: {len(readings)} reading(s)")

        recorded = {words(m.mark) for m in self.marks()}
        standing = {m.id for m in self.marks()}
        for m in update.marks:
            text = " ".join(m.mark.split())
            cited = sorted({i for i in m.inputs if i in inputs})
            if not text or words(text) in recorded:
                continue
            if not cited:
                notes.append(f"viewer mark not kept, it cites no input: {text}")
                continue
            # A mark entered the story where the first clip that answered
            # its inputs did.
            entered = min((o for o in self.outcomes() if set(o.inputs) & set(cited)),
                          key=lambda o: o.clip, default=None)
            entities = world.mentions(" ".join([text, *(inputs[i].text for i in cited)]))
            shown = next((c for c in world.clips if entered is not None and c.clip == entered.clip), None)
            if shown is not None:
                entities |= set(shown.on_screen)
                entities |= {e.id for e in world.entities.values() if e.first_seen == shown.clip}
            replaces = m.replaces if m.replaces in standing else 0
            if m.replaces and not replaces:
                notes.append(f"mark {m.replaces} is not a standing mark; nothing replaced")
            mark = self.add(Mark(
                id=max((m.id for m in self.all_marks()), default=0) + 1,
                clip=entered.clip if entered else at_clip,
                mark=text, inputs=cited, entities=sorted(entities), replaces=replaces,
            ))
            recorded.add(words(text))
            standing.discard(replaces)
            standing.add(mark.id)
            notes.append(f"viewer's mark recorded: {text}" + (f" (replacing mark {replaces})" if replaces else ""))
        return notes
