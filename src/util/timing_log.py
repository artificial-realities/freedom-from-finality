"""Per-session timing log: what the live loop lives by, one JSON line per event.

`logs/sessions/<id>/timing.jsonl` records when each clip was fired,
rendered, queued, put on air and taken off; when each viewer line arrived,
was taken by Now and landed; what each Now call cost; and
the browser's own view of each swap. Nothing reads it back during a run.
It exists so a benchmark can be computed from a film instead of being
reconstructed from file times.

Every record carries `t` (seconds since the log opened, monotonic),
`wall` (Unix time, to line records up with the browser and the server)
and `event`; an event's own fields cannot overwrite them. Writing never
raises: a log that cannot be written is switched off with one warning,
and the film goes on.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]


class TimingLog:
    def __init__(self, path: Path | str | None) -> None:
        """`path` None gives a log that records nothing (callers that
        were not handed one)."""
        self.path = Path(path) if path is not None else None
        self._t0 = time.monotonic()
        self._fh: IO[str] | None = None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
            except OSError as exc:
                logger.warning("Timing log %s cannot be opened (%s); timing is not recorded", self.path, exc)

    def write(self, event: str, **fields) -> None:
        if self._fh is None:
            return
        now = time.monotonic()
        record = {"t": round(now - self._t0, 4), "wall": round(time.time(), 4), "event": event}
        clash = [key for key in fields if key in record]
        if clash:
            logger.warning("Timing log: %s fields %s clash with the record's own and are dropped", event, clash)
        record.update((key, value) for key, value in fields.items() if key not in clash)
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()
        except (OSError, ValueError) as exc:
            logger.warning("Timing log %s stopped: write failed (%s)", self.path, exc)
            self.close()

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass


def git_revision() -> tuple[str | None, bool | None]:
    """(commit, dirty) of the code this process runs, or (None, None) where
    there is no git checkout (the GPU box receives an exported tree)."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, timeout=5)
        if head.returncode != 0:
            return None, None
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                                capture_output=True, text=True, timeout=5)
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return head.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return None, None
