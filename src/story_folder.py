"""The viewer's story on disk: everything needed to go on telling it.

A story begins once, from Frame Zero, and goes on for good. It lives in
one portable folder that a copy backs up or moves to another computer,
and holds what the story needs to continue, never the rendered video:

    story.json            format, id, kind (video, or text for a test
                          story)
    frame_zero/           the Frame Zero it began from, copied once
    viewer.jsonl          the viewer's memory (`src/story/viewer.py`)
    clips.jsonl           every clip the viewer saw, one per line
    history.jsonl         Then's summaries and checkpoints, one per line
                          (`src/story/history.py`)
    state/<clip>/         the saved state, the newest only
        world.json        the world as of that clip
        last_frames.mp4   the frames the renderer continues the picture
                          from (a video story)

It is saved as of the last clip the viewer saw. When a clip goes on air
the history records its world has seen are appended, its state is
written under `state/<clip>.tmp/`, synced and renamed into place (the
commit), the state before it is let go and the clip is appended to the
clip log; what the producer prepared beyond it is never saved. Opening
takes the newest complete state and makes the clip log and the history
agree with it, so a crash anywhere costs at most the clips not yet seen.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from src.models.frame_zero import FrameZero
from src.story.history import History
from src.story.viewer import ViewerMemory
from src.story.world import ClipRecord, WorldModel
from src.util.durable import append_line, read_jsonl, replace, sync_dir, write_synced

logger = logging.getLogger(__name__)

FORMAT = 3
Kind = Literal["video", "text"]


class StoryFolderError(RuntimeError):
    """The story cannot be opened or replaced; the message says why."""


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: int = FORMAT
    id: str
    kind: Kind


class Contents(BaseModel):
    """What a story folder holds, read without opening it: the last clip
    the viewer saw (0: none yet)."""

    id: str
    kind: Kind
    clip: int


class StoryFolder:
    """An open story: its Frame Zero, the viewer's memory, and the story as
    of the last clip the viewer saw: its world as it was loaded (a film's
    engine owns the live one), that clip (`last`) and, for video, the
    frames the picture continues from (`last_frames`). One process opens
    it at a time."""

    def __init__(self, root: Path, manifest: Manifest, frame_zero: FrameZero, viewer: ViewerMemory) -> None:
        self.root = root
        self.manifest = manifest
        self.frame_zero = frame_zero
        self.viewer = viewer
        self.world = WorldModel()
        self.history = History(root / "history.jsonl")
        self.last: ClipRecord | None = None
        self.last_frames: Path | None = None

    @property
    def id(self) -> str:
        return self.manifest.id

    @classmethod
    def open(cls, root: Path | str, *, kind: Kind, frame_zero: Path | str) -> "StoryFolder":
        """The story in `root`, begun from `frame_zero` when there is none
        yet: the only time Frame Zero is read. `kind` must be the one it
        began as, so a test story and the viewer's never mix."""
        root = Path(root)
        _finish_adoption(root)
        if not (root / "story.json").exists():
            _create(root, kind=kind, frame_zero=frame_zero)
        manifest = _manifest(root)
        if manifest.kind != kind:
            raise StoryFolderError(f"{root} is a {manifest.kind} story; it cannot be played as {kind}")
        _lock(root)
        try:
            return cls._load(root, manifest)
        except BaseException:
            _unlock(root)
            raise

    @classmethod
    def _load(cls, root: Path, manifest: Manifest) -> "StoryFolder":
        frame_zero = FrameZero.from_json(root / "frame_zero" / "frame_zero.json")
        folder = cls(root, manifest, frame_zero, ViewerMemory.load(root / "viewer.jsonl"))
        folder.reload()
        return folder

    def reload(self) -> None:
        """Read the story as of the last clip the viewer saw, from disk, for
        the next film: nothing a film prepared beyond the screen carries
        over."""
        newest = _newest(self.root / "state", clean=True)
        if newest is None:
            self.world, self.last_frames = WorldModel.from_frame_zero(self.frame_zero), None
        else:
            state, self.world = newest
            self.last_frames = state / "last_frames.mp4" if (state / "last_frames.mp4").exists() else None
        _reconcile(self.root / "clips.jsonl", self.world)
        self.history = History.load(self.root / "history.jsonl", seen=self.world.history_count)
        self.world.history_count = len(self.history.records)
        self.last = self.world.clips[-1] if self.world.clips else None

    def frame_zero_image(self) -> Path | None:
        """The image Frame Zero shows, as it was copied into the story."""
        name = self.frame_zero.reference_image
        path = self.root / "frame_zero" / Path(name).name if name else None
        return path if path is not None and path.exists() else None

    def commit(self, clip_no: int, world_json: str, clip_json: str, last_frames: Path | None, *,
               history_upto: int) -> None:
        """Save the story as of clip `clip_no`, the newest the viewer has
        seen: the history records its world has seen (`history_upto`), the
        world then and, for video, the frames that continue it
        (`last_frames`, a file this takes over). A clip whose frames could
        not be cut keeps the last ones the picture can continue from."""
        self.history.save(history_upto)
        states = self.root / "state"
        tmp, final = states / f"{clip_no:06d}.tmp", states / f"{clip_no:06d}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        write_synced(tmp / "world.json", world_json.encode("utf-8"))
        frames = last_frames if last_frames is not None else self.last_frames
        if frames is not None and self.manifest.kind == "video":
            write_synced(tmp / "last_frames.mp4", frames.read_bytes())
        sync_dir(tmp)
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)                       # the commit
        sync_dir(states)
        for d in states.iterdir():
            if d.is_dir() and d.name.isdigit() and d != final:
                shutil.rmtree(d, ignore_errors=True)
        append_line(self.root / "clips.jsonl", clip_json)
        self.last = ClipRecord.model_validate_json(clip_json)
        self.last_frames = final / "last_frames.mp4" if (final / "last_frames.mp4").exists() else None

    def close(self) -> None:
        _unlock(self.root)


def describe(root: Path | str) -> Contents | None:
    """What the folder holds, read without opening it (no lock, nothing
    changed); None when it holds no story."""
    root = Path(root)
    if not (root / "story.json").exists():
        return None
    manifest = _manifest(root)
    newest = _newest(root / "state", clean=False)
    clip = newest[1].clips[-1].clip if newest is not None and newest[1].clips else 0
    return Contents(id=manifest.id, kind=manifest.kind, clip=clip)


def adopt(incoming: Path | str, root: Path | str) -> str:
    """Make `incoming`, a copy of the story brought back from another
    machine (the GPU box), the story in `root`, if it is the same story and
    not behind it. It must sit next to `root`, so the swap is two renames
    on one disk. The story it replaces is kept as `<root>.previous` until
    the next one. Returns what changed."""
    incoming, root = Path(incoming), Path(root)
    new = describe(incoming)
    if new is None:
        raise StoryFolderError(f"{incoming} holds no story")
    old = describe(root)
    if old is not None:
        if new.id != old.id:
            raise StoryFolderError(f"{incoming} is a different story from yours; yours was kept")
        if new.clip < old.clip:
            raise StoryFolderError(f"the copy is behind yours (clip {new.clip}, yours {old.clip}); yours was kept")
        if _held(root / ".lock"):
            raise StoryFolderError("your story is open in FFF on this computer; close it first")
    (incoming / ".lock").unlink(missing_ok=True)
    previous = root.with_name(root.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)
    if root.exists():
        root.rename(previous)
    incoming.rename(root)
    sync_dir(root.parent)
    return f"story updated: clip {new.clip}" + (f" (was {old.clip})" if old is not None else "")


# ── On disk ─────────────────────────────────────────────────────────

def _manifest(root: Path) -> Manifest:
    try:
        manifest = Manifest.model_validate_json((root / "story.json").read_bytes())
    except ValidationError as exc:
        raise StoryFolderError(f"{root / 'story.json'} is not readable: {exc}") from None
    if manifest.format != FORMAT:
        newer = manifest.format > FORMAT
        raise StoryFolderError(f"{root} was written by {'a newer' if newer else 'an older'} FFF "
                               f"(format {manifest.format})" + ("; update FFF to open it" if newer else ""))
    return manifest


def _create(root: Path, *, kind: Kind, frame_zero: Path | str) -> None:
    """Begin a story in `root` from `frame_zero`. It is built under
    `<root>.creating` and renamed into place, so a crash leaves either
    nothing or a whole story."""
    if root.exists() and any(root.iterdir()):
        raise StoryFolderError(f"{root} is not empty and holds no story (no story.json); "
                               "choose another --story or move it aside")
    fz = FrameZero.from_json(frame_zero)
    building = root.with_name(root.name + ".creating")
    shutil.rmtree(building, ignore_errors=True)
    copy = building / "frame_zero"
    copy.mkdir(parents=True)
    if fz.reference_image:
        image = Path(fz.reference_image)
        shutil.copyfile(image, copy / image.name)
        fz.reference_image = image.name
    write_synced(copy / "frame_zero.json", fz.model_dump_json(indent=2).encode("utf-8"))
    write_synced(building / "story.json",
                 Manifest(id=uuid.uuid4().hex, kind=kind).model_dump_json(indent=2).encode("utf-8"))
    if root.exists():
        root.rmdir()
    building.rename(root)
    sync_dir(root.parent)
    logger.info("A new story begins in %s", root)


def _finish_adoption(root: Path) -> None:
    """An adoption cut short between its two renames leaves the story only
    as `<root>.previous`: put it back rather than begin a new one."""
    previous = root.with_name(root.name + ".previous")
    if not (root / "story.json").exists() and (previous / "story.json").exists():
        if root.exists():
            root.rmdir()
        previous.rename(root)
        logger.warning("Restored the story in %s from %s (a replacement was cut short)", root, previous)


def _newest(states: Path, *, clean: bool) -> tuple[Path, WorldModel] | None:
    """The newest complete saved state and its world. With `clean`, what a
    crash left behind goes: unfinished commits (`*.tmp`) are removed, and
    a newer state that does not load is set aside as `*.broken`."""
    if not states.exists():
        return None
    if clean:
        for d in states.glob("*.tmp"):
            shutil.rmtree(d, ignore_errors=True)
    for d in sorted((d for d in states.iterdir() if d.is_dir() and d.name.isdigit()),
                    key=lambda d: int(d.name), reverse=True):
        try:
            return d, WorldModel.model_validate_json((d / "world.json").read_bytes())
        except (OSError, ValidationError) as exc:
            logger.warning("Saved state %s does not load (%s); using an older one", d, str(exc)[:200])
            if clean:
                d.rename(d.with_name(d.name + ".broken"))
    return None


def _reconcile(path: Path, world: WorldModel) -> None:
    """Make the clip log agree with the world it resumes from: drop lines
    that do not parse or lie beyond the world's last clip (an older state
    was used), and add the clips the world has that the log lacks (a
    commit cut short after its rename)."""
    head = world.clips[-1].clip if world.clips else 0
    on_disk = [line for line in path.read_bytes().splitlines() if line.strip()] if path.exists() else []
    kept: list[ClipRecord] = []
    for raw in read_jsonl(path):
        try:
            record = ClipRecord.model_validate(raw)
        except ValidationError:
            continue
        if record.clip <= head:
            kept.append(record)
    if len(kept) != len(on_disk):
        logger.warning("The clip log in %s disagreed with the saved state; repaired", path.parent)
        replace(path, "".join(c.model_dump_json() + "\n" for c in kept).encode("utf-8"))
    last = kept[-1].clip if kept else 0
    for record in world.clips:
        if record.clip > last:
            append_line(path, record.model_dump_json())


def _lock(root: Path) -> None:
    """Take the story for this process. A lock left by a process that is
    gone, or by another machine (a copied folder), is stale and replaced."""
    path = root / ".lock"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _held(path):
                raise StoryFolderError(f"the story in {root} is open in another FFF "
                                       f"({path.read_text(errors='replace').strip()}); close it first") from None
            logger.warning("Replacing a stale lock on %s", root)
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            f.write(f"{socket.gethostname()} {os.getpid()}")
        return
    raise StoryFolderError(f"could not lock the story in {root}")


def _held(path: Path) -> bool:
    """Whether the lock at `path` belongs to a live process on this machine
    (on Windows, any lock from this machine: delete `.lock` if no FFF runs)."""
    try:
        host, pid = path.read_text(errors="replace").split()
    except (OSError, ValueError):
        return False
    if host != socket.gethostname() or not pid.isdigit():
        return False
    if os.name == "nt":
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _unlock(root: Path) -> None:
    path = root / ".lock"
    try:
        if path.read_text(errors="replace").split()[-1] == str(os.getpid()):
            path.unlink()
    except (OSError, IndexError):
        pass
