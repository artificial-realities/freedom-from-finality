"""The story's long memory: Then's summaries and checkpoints, for good.

Then writes a **summary** of the clips since its last run (about every
ten clips) and, when it sees the story has turned, names a
**checkpoint**: the clip it turned on and a summary of the story from the
checkpoint before (or the beginning) up to there. Each record also keeps
the entities it involves, which code takes from the clips it covers (who
and what was on screen, the places, who was named), so that when one of
them comes back into the story, the part of the past it was in can be
put in front of the roles again (`context.coming_back`).

The world model holds only what is current (the story so far, since the
last checkpoint) and how many of these records it has seen
(`WorldModel.history_count`); the records themselves are kept here,
append-only, one per line in `history.jsonl`, so the world saved with
every clip stays small however long the story runs. The story folder
appends the records a clip's world has seen before it commits that clip,
and opening trims the file back to what the saved world has seen.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from src.util.durable import append_line, read_jsonl, replace

logger = logging.getLogger(__name__)


class Summary(BaseModel):
    """Clips `first_clip`-`last_clip`, as Then summarised them."""

    kind: Literal["summary"] = "summary"
    first_clip: int
    last_clip: int
    text: str
    entities: list[str] = Field(default_factory=list)


class Checkpoint(BaseModel):
    """Where the story turned: clip `at`, which Then named `name`, and its
    summary of the story from `first_clip` (after the checkpoint before)
    up to there. `noticed_at` is the clip Then had read up to."""

    kind: Literal["checkpoint"] = "checkpoint"
    name: str
    first_clip: int
    at: int
    summary: str
    noticed_at: int = 0
    entities: list[str] = Field(default_factory=list)


Record = Annotated[Summary | Checkpoint, Field(discriminator="kind")]
_RECORD = TypeAdapter(Record)


class History:
    """Every summary and checkpoint, oldest first. `on_disk` is how many of
    them `history.jsonl` holds."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: list[Summary | Checkpoint] = []
        self.on_disk = 0

    @classmethod
    def load(cls, path: Path, *, seen: int) -> "History":
        """The records saved at `path`, as many as the saved world has seen
        (`seen`): a crash after the records were appended and before their
        clip was committed leaves more, and those are dropped from the file."""
        history = cls(path)
        for raw in read_jsonl(path):
            try:
                history.records.append(_RECORD.validate_python(raw))
            except ValidationError:
                logger.warning("A record in %s is not a history record; skipped: %s", path, str(raw)[:200])
        if len(history.records) > seen:
            logger.warning("%s holds %d records the saved state never saw; dropped",
                           path, len(history.records) - seen)
            del history.records[seen:]
            replace(path, "".join(r.model_dump_json() + "\n" for r in history.records).encode("utf-8"))
        elif len(history.records) < seen:
            logger.warning("%s holds %d records, the saved state saw %d; going on with those", path,
                           len(history.records), seen)
        history.on_disk = len(history.records)
        return history

    def save(self, upto: int) -> None:
        """Append the records up to `upto` the file does not hold yet."""
        if self.path is None:
            return
        for record in self.records[self.on_disk:upto]:
            append_line(self.path, record.model_dump_json())
        self.on_disk = max(self.on_disk, min(upto, len(self.records)))

    def truncate(self, count: int) -> None:
        """Forget the records after the first `count`: the world they landed
        in was rolled back. Never below what the file holds."""
        del self.records[max(count, self.on_disk):]

    def view(self, count: int) -> "History":
        """The first `count` records, apart from this history: what a Then
        run reads while the story goes on."""
        view = History()
        view.records = list(self.records[:count])
        return view

    def summaries(self) -> list[Summary]:
        return [r for r in self.records if isinstance(r, Summary)]

    def checkpoints(self) -> list[Checkpoint]:
        return [r for r in self.records if isinstance(r, Checkpoint)]

    def last_checkpoint(self) -> Checkpoint | None:
        return next((r for r in reversed(self.records) if isinstance(r, Checkpoint)), None)
