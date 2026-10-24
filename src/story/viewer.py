"""The viewer's memory: what the person watching has said and put into
the story, and Then's understanding of them.

It belongs to the person, not to a timeline. The world model is one
timeline, which a checkpoint or a branch may rewind or fork; this memory
is never rewound or copied. Every record carries the timeline and the
segment it happened at, so a view for one timeline can tell its own
history from another's. Nothing is edited or removed: the memory is
append-only, saved one record per line to `viewer.jsonl`, each flushed
to disk before the next, so a crash costs at most the record being
written, and that only damages its own line.

Facts and interpretation are kept apart. Code writes the facts: each line
as it arrives with the clip on air then (`Said`), what the story did with
it once that clip reached the screen (`Answer`), and when a direction
ended (`DirectionEnd`). Then writes the interpretation: its understanding
of the viewer, each reading citing the lines it rests on
(`Understanding`), and the viewer's marks on the story (`Mark`).
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


class Said(BaseModel):
    """A line as the viewer typed it. `segment` is the clip on air then
    (0 before the first), `during` what that clip showed, and `entities`
    who and what was in it or named by the line."""

    kind: Literal["said"] = "said"
    id: int
    timeline: str
    segment: int
    text: str
    during: str = ""
    entities: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    """What the story did with lines (`said`), recorded when the clip that
    did it reached the screen: absorbed, deferred or declined at first, and
    a deferred line again when a later beat delivers it."""

    kind: Literal["answer"] = "answer"
    said: list[int]
    timeline: str
    segment: int
    disposition: str
    reason: str = ""
    beat: str = ""


class DirectionEnd(BaseModel):
    """The story stopped building on a direction: Now judged it fulfilled
    or run its course, or a newer line took its place."""

    kind: Literal["direction_end"] = "direction_end"
    said: list[int]
    timeline: str
    segment: int
    how: Literal["fulfilled", "ran its course", "superseded"]


class Mark(BaseModel):
    """Something the viewer put into the story, as Then noticed it: a name,
    a decision, a relationship, a change. `segment` is where it entered the
    story; `entities` what it touches, for finding it again."""

    kind: Literal["mark"] = "mark"
    id: int
    timeline: str
    segment: int
    mark: str
    said: list[int]
    entities: list[str] = Field(default_factory=list)


class Understanding(ViewerUnderstanding):
    """Then's understanding of the viewer, landed at `segment`. `seen` is
    how many records the memory held when Then was given it to read:
    whatever came after is new to its next run."""

    kind: Literal["understanding"] = "understanding"
    timeline: str
    segment: int
    seen: int = 0


Record = Annotated[Said | Answer | DirectionEnd | Mark | Understanding, Field(discriminator="kind")]
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

    def lines(self) -> list[Said]:
        return [r for r in self.records if isinstance(r, Said)]

    def outcomes(self) -> list[Answer | DirectionEnd]:
        return [r for r in self.records if isinstance(r, (Answer, DirectionEnd))]

    def marks(self) -> list[Mark]:
        return [r for r in self.records if isinstance(r, Mark)]

    def understanding(self) -> Understanding | None:
        return next((r for r in reversed(self.records) if isinstance(r, Understanding)), None)

    def unanswered(self, timeline: str) -> list[Said]:
        """The lines typed in `timeline` after the story last answered one
        there that it never answered: what the viewer said just before the
        story stopped."""
        answered = {i for o in self.outcomes() if isinstance(o, Answer) for i in o.said}
        lines = [s for s in self.lines() if s.timeline == timeline]
        last = max((s.id for s in lines if s.id in answered), default=0)
        return [s for s in lines if s.id > last and s.id not in answered]

    # ── Writing ─────────────────────────────────────────────────────

    def hear(self, text: str, *, timeline: str, segment: int, during: str, entities: set[str]) -> Said:
        line = Said(id=max((s.id for s in self.lines()), default=0) + 1, timeline=timeline, segment=segment,
                    text=" ".join(text.split()), during=during, entities=sorted(entities))
        return self.add(line)

    def land(self, update: ThenUpdate, world: WorldModel, *, at_segment: int, seen: int) -> list[str]:
        """Land Then's understanding of the viewer and its new marks. A
        reading must cite a line it rests on and a mark the lines it came
        from: ids that name no line are dropped, and a reading or mark left
        citing none is not kept. A mark already recorded is not recorded
        again."""
        notes: list[str] = []
        lines = {line.id: line for line in self.lines()}
        readings: list[Reading] = []
        for r in update.viewer.readings:
            text = " ".join(r.reading.split())
            grounds = sorted({g for g in r.grounds if g in lines})
            if text and grounds:
                readings.append(Reading(reading=text, grounds=grounds))
            elif text:
                notes.append(f"viewer reading not kept, it cites no line: {text}")
        self.add(Understanding(readings=readings, lately=" ".join(update.viewer.lately.split()),
                               unknown=" ".join(update.viewer.unknown.split()),
                               timeline=world.timeline, segment=at_segment, seen=seen))
        notes.append(f"understanding of the viewer: {len(readings)} reading(s)")

        recorded = {words(m.mark) for m in self.marks()}
        for m in update.marks:
            text = " ".join(m.mark.split())
            said = sorted({i for i in m.said if i in lines})
            if not text or words(text) in recorded:
                continue
            if not said:
                notes.append(f"viewer mark not kept, it cites no line: {text}")
                continue
            # A mark entered the story where the first clip that answered
            # its lines did, in that clip's timeline.
            entered = min((a for a in self.outcomes() if isinstance(a, Answer) and set(a.said) & set(said)),
                          key=lambda a: a.segment, default=None)
            self.add(Mark(
                id=max((m.id for m in self.marks()), default=0) + 1,
                timeline=entered.timeline if entered else world.timeline,
                segment=entered.segment if entered else at_segment,
                mark=text, said=said,
                entities=sorted(world.mentions(" ".join([text, *(lines[i].text for i in said)]))),
            ))
            recorded.add(words(text))
            notes.append(f"viewer's mark recorded: {text}")
        return notes
