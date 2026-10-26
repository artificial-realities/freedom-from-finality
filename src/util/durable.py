"""Writes that survive a crash: the story is meant to last a lifetime.

Append-only logs get one record per line, synced before the call returns,
each on a line of its own, so a write cut off by a crash damages only
itself and a reader skips it. Whole files are written to a temporary name,
synced and renamed into place, so they are either the old file or the new
one, never half of each.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def append_line(path: Path, line: str) -> None:
    """Append one line and make it durable before returning. A line always
    starts on a line of its own, so one cut off by a crash damages only
    itself."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        f.write(line.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


def write_synced(path: Path, data: bytes) -> None:
    """Write a file and make it durable (not atomic by itself: see `replace`)."""
    with path.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def replace(path: Path, data: bytes) -> None:
    """Replace a file whole: the old one or the new one, never half of each."""
    tmp = path.with_name(path.name + ".tmp")
    write_synced(tmp, data)
    os.replace(tmp, path)
    sync_dir(path.parent)


def sync_dir(path: Path) -> None:
    """Make a rename or a new file in `path` durable (POSIX; Windows has no
    directory handle to sync, and its renames are durable on their own)."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    """Every record of a JSON-lines file, skipping a line a crash cut off
    (or any other that does not parse); [] when there is no file."""
    if not path.exists():
        return []
    records = []
    for n, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            logger.warning("%s line %d does not parse (a write cut off by a crash?); skipped", path, n)
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
