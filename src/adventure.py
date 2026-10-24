"""One viewer's adventure on disk: everything needed to go on telling it.

An adventure begins once, from the frame zero, and goes on for good. It
lives in one portable folder that a copy backs up or moves to another
computer, and holds what the story needs to continue, never the rendered
video:

    adventure.json        format, id, kind (video, or text for a test
                          adventure) and the current timeline
    origin/               the frame zero it began from, copied once
    viewer.jsonl          the viewer's memory (`src/story/viewer.py`)
    timelines/<id>/
        timeline.json     its parent and the segment it forked at (none
                          for the root, the only timeline so far)
        events.jsonl      every clip the viewer saw: the story's history
        continuations/<segment>/
            world.json    the world as of that clip
            window.mp4    the frames the renderer continues the picture
                          from (a video adventure)
        checkpoints/<segment>/
            checkpoint.json   the turning point: its era, where and who
            world.json        the world as of that clip
            window.mp4        the frames the picture continues from
            still.png         the last of them, the picture it goes on from

It is saved as of the last clip the viewer saw. When a clip goes on air
its continuation is written under `<segment>.tmp/`, synced and renamed
into place (the commit) and its event is appended; what the producer
prepared beyond it is never saved. Opening takes the newest complete
continuation and makes the event log agree with it, so a crash anywhere
costs at most the clips not yet seen.

The newest continuation is the latest position, there to recover from.
The ones before it are kept only until Then has had its chance to name a
turning point among them (it reads every clip since it last summarised),
at most RING_MAX: a turning point it names there becomes a story
checkpoint, kept for good, which the viewer can return to. One it names
at a moment no longer kept, or at a clip that is not the one that aired,
stays an era without a checkpoint; it is never moved to a nearby clip.
A branch will be a new timeline that starts from a checkpoint.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.models.story import FrameZero
from src.story.viewer import ViewerMemory
from src.story.world import ROOT_TIMELINE, Era, EventRecord, WorldModel
from src.util.durable import append_line, read_jsonl, replace, sync_dir, write_synced
from src.util.media import last_frame

logger = logging.getLogger(__name__)

FORMAT = 1
Kind = Literal["video", "text"]
# Recent moments kept for Then to name a turning point among, at most:
# about ten in the usual run of things (the clips since Then last
# summarised); more only while its runs keep failing.
RING_MAX = 60


class AdventureError(RuntimeError):
    """The adventure cannot be opened or replaced; the message says why."""


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: int = FORMAT
    id: str
    kind: Kind
    timeline: str = ROOT_TIMELINE


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    parent: str | None = None
    forked_at: int | None = None


class Checkpoint(BaseModel):
    """A story checkpoint: the clip of `segment` in `timeline`, where the era
    `name` began, kept for good so the viewer can return to that moment.
    `place`, `on_screen` and `picture` are what that clip showed, for the
    map; the full world is next to it."""

    model_config = ConfigDict(extra="forbid")

    timeline: str
    segment: int
    clip_id: int
    name: str
    chronicle: str
    recognised_at: int
    place: str = ""
    on_screen: list[str] = Field(default_factory=list)
    picture: str = ""
    created_at: str


class Summary(BaseModel):
    """What an adventure folder holds, read without opening it. `segment`
    is the last clip the viewer saw (0: none yet)."""

    id: str
    kind: Kind
    timeline: str
    segment: int


class Adventure:
    """An open adventure: its origin, the world as of the last clip the
    viewer saw, the viewer's memory and, for video, the frames the picture
    continues from (`window`). One process opens it at a time."""

    def __init__(self, root: Path, manifest: Manifest, origin: FrameZero, world: WorldModel,
                 viewer: ViewerMemory, window: Path | None) -> None:
        self.root = root
        self.manifest = manifest
        self.origin = origin
        self.world = world
        self.viewer = viewer
        self.window = window
        # Turning points that could not be kept, so each is reported once.
        self._unkept: set[tuple[str, int]] = set()

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def timeline_dir(self) -> Path:
        return self.root / "timelines" / self.manifest.timeline

    @classmethod
    def open(cls, root: Path | str, *, kind: Kind, frame_zero: Path | str) -> "Adventure":
        """The adventure in `root`, begun from `frame_zero` when there is none
        yet: the only time the frame zero is read. `kind` must be the one it
        began as, so a test adventure and the viewer's never mix."""
        root = Path(root)
        _finish_adoption(root)
        if not (root / "adventure.json").exists():
            _create(root, kind=kind, frame_zero=frame_zero)
        manifest = _manifest(root)
        if manifest.kind != kind:
            raise AdventureError(f"{root} is a {manifest.kind} adventure; it cannot be played as {kind}")
        _lock(root)
        try:
            return cls._load(root, manifest)
        except BaseException:
            _unlock(root)
            raise

    @classmethod
    def _load(cls, root: Path, manifest: Manifest) -> "Adventure":
        origin = FrameZero.from_json(root / "origin" / "frame_zero.json")
        timeline = root / "timelines" / manifest.timeline
        head = _newest(timeline / "continuations", clean=True)
        if head is None:
            world, window = WorldModel.from_frame_zero(origin), None
            world.timeline = manifest.timeline
        else:
            folder, world = head
            window = folder / "window.mp4" if (folder / "window.mp4").exists() else None
        _reconcile(timeline / "events.jsonl", world)
        for d in (timeline / "checkpoints").glob("*.tmp"):
            shutil.rmtree(d, ignore_errors=True)
        adventure = cls(root, manifest, origin, world, ViewerMemory.load(root / "viewer.jsonl"), window)
        # A commit cut short after its rename may not have kept its checkpoint.
        adventure._settle(world)
        return adventure

    def commit(self, segment: int, world_json: str, event_json: str, window: Path | None) -> None:
        """Save the adventure as of the clip of `segment`, the newest the
        viewer has seen: the world then and, for video, the frames that
        continue it (`window`, a file this takes over). A clip whose frames
        could not be cut keeps the last ones the picture can continue from."""
        conts = self.timeline_dir / "continuations"
        tmp, final = conts / f"{segment:06d}.tmp", conts / f"{segment:06d}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        write_synced(tmp / "world.json", world_json.encode("utf-8"))
        frames = window if window is not None else self.window
        if frames is not None and self.manifest.kind == "video":
            write_synced(tmp / "window.mp4", frames.read_bytes())
        sync_dir(tmp)
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)                       # the commit
        sync_dir(conts)
        append_line(self.timeline_dir / "events.jsonl", event_json)
        self.window = final / "window.mp4" if (final / "window.mp4").exists() else None
        self._settle(WorldModel.model_validate_json(world_json))

    def kept_from(self) -> int | None:
        """The oldest clip whose moment is still kept, where a turning point
        can be named at the earliest (None: nothing saved yet)."""
        ring = self._ring()
        return ring[0] if ring else None

    def _ring(self) -> list[int]:
        """The segments of the continuations kept, oldest first."""
        conts = self.timeline_dir / "continuations"
        if not conts.exists():
            return []
        return sorted(int(d.name) for d in conts.iterdir() if d.is_dir() and d.name.isdigit())

    def _settle(self, world: WorldModel) -> None:
        """After a commit of `world`: keep a checkpoint of each turning point
        it holds in this timeline that has none yet, then let go of the
        moments Then has had its chance to name (those up to where it last
        summarised), keeping the newest and at most RING_MAX."""
        for era in world.eras:
            if era.timeline == self.manifest.timeline:
                self._keep(era)
        ring = self._ring()
        if not ring:
            return
        keep = set([s for s in ring if s > world.last_compacted or s == ring[-1]][-RING_MAX:])
        for seg in ring:
            if seg not in keep:
                shutil.rmtree(self.timeline_dir / "continuations" / f"{seg:06d}", ignore_errors=True)

    def _keep(self, era: Era) -> None:
        """Keep the moment of `era` as a story checkpoint, from the
        continuation of its clip, if that is still kept and is the clip
        Then read. Built under `<segment>.tmp/` and renamed into place."""
        key = (era.timeline, era.turned_at)
        folder = self.timeline_dir / "checkpoints" / f"{era.turned_at:06d}"
        if folder.exists() or key in self._unkept:
            return
        moment = self.timeline_dir / "continuations" / f"{era.turned_at:06d}"
        world = None
        try:
            world = WorldModel.model_validate_json((moment / "world.json").read_bytes())
        except (OSError, ValidationError):
            why = "that moment is no longer kept"
        else:
            ev = world.events[-1] if world.events else None
            if not era.clip_id:
                why = "Then did not read that clip"
            elif ev is None or ev.segment != era.turned_at or ev.clip_id != era.clip_id:
                why = "the clip Then read there never aired"
            else:
                why = ""
        if why:
            self._unkept.add(key)
            logger.warning("Turning point %r at segment %d is not a checkpoint: %s",
                           era.name, era.turned_at, why)
            return
        ev = world.events[-1]
        names = [world.entities[i].name for i in ev.on_screen if i in world.entities]
        place = world.entities.get(ev.location)
        checkpoint = Checkpoint(
            timeline=era.timeline, segment=era.turned_at, clip_id=era.clip_id, name=era.name,
            chronicle=era.chronicle, recognised_at=era.recognised_at, place=place.name if place else "",
            on_screen=names, picture=ev.narration,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        tmp = folder.with_name(folder.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        write_synced(tmp / "world.json", (moment / "world.json").read_bytes())
        if (moment / "window.mp4").exists():
            write_synced(tmp / "window.mp4", (moment / "window.mp4").read_bytes())
            try:
                last_frame(tmp / "window.mp4", tmp / "still.png")
            except Exception as exc:
                logger.warning("Checkpoint at segment %d keeps no still: %s", era.turned_at, exc)
                (tmp / "still.png").unlink(missing_ok=True)
        write_synced(tmp / "checkpoint.json", checkpoint.model_dump_json(indent=2).encode("utf-8"))
        sync_dir(tmp)
        tmp.rename(folder)
        sync_dir(folder.parent)
        logger.info("Checkpoint kept: %r at segment %d", era.name, era.turned_at)

    def close(self) -> None:
        _unlock(self.root)


def describe(root: Path | str) -> Summary | None:
    """What the folder holds, read without opening it (no lock, nothing
    changed); None when it holds no adventure."""
    root = Path(root)
    if not (root / "adventure.json").exists():
        return None
    manifest = _manifest(root)
    head = _newest(root / "timelines" / manifest.timeline / "continuations", clean=False)
    segment = head[1].events[-1].segment if head is not None and head[1].events else 0
    return Summary(id=manifest.id, kind=manifest.kind, timeline=manifest.timeline, segment=segment)


def adopt(incoming: Path | str, root: Path | str) -> str:
    """Make `incoming`, a copy of the adventure brought back from another
    machine (the GPU box), the adventure in `root`, if it is the same
    adventure and not behind it. It must sit next to `root`, so the swap is
    two renames on one disk. The adventure it replaces is kept as
    `<root>.previous` until the next one. Returns what changed."""
    incoming, root = Path(incoming), Path(root)
    new = describe(incoming)
    if new is None:
        raise AdventureError(f"{incoming} holds no adventure")
    old = describe(root)
    if old is not None:
        if new.id != old.id:
            raise AdventureError(f"{incoming} is a different adventure from yours; yours was kept")
        if new.timeline == old.timeline and new.segment < old.segment:
            raise AdventureError(f"the copy is behind yours (segment {new.segment}, yours {old.segment}); yours was kept")
        if _held(root / ".lock"):
            raise AdventureError("your adventure is open in FFF on this computer; close it first")
    (incoming / ".lock").unlink(missing_ok=True)
    previous = root.with_name(root.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)
    if root.exists():
        root.rename(previous)
    incoming.rename(root)
    sync_dir(root.parent)
    return f"adventure updated to segment {new.segment}" + (f" (was {old.segment})" if old is not None else "")


# ── On disk ─────────────────────────────────────────────────────────

def _manifest(root: Path) -> Manifest:
    try:
        manifest = Manifest.model_validate_json((root / "adventure.json").read_bytes())
    except ValidationError as exc:
        raise AdventureError(f"{root / 'adventure.json'} is not readable: {exc}") from None
    if manifest.format > FORMAT:
        raise AdventureError(f"{root} was written by a newer FFF (format {manifest.format}); update FFF to open it")
    return manifest


def _create(root: Path, *, kind: Kind, frame_zero: Path | str) -> None:
    """Begin an adventure in `root` from `frame_zero`. It is built under
    `<root>.creating` and renamed into place, so a crash leaves either
    nothing or a whole adventure."""
    if root.exists() and any(root.iterdir()):
        raise AdventureError(f"{root} is not empty and holds no adventure (no adventure.json); "
                             "choose another --adventure or move it aside")
    fz = FrameZero.from_json(frame_zero)
    building = root.with_name(root.name + ".creating")
    shutil.rmtree(building, ignore_errors=True)
    origin = building / "origin"
    origin.mkdir(parents=True)
    if fz.reference_image:
        image = Path(fz.reference_image)
        shutil.copyfile(image, origin / image.name)
        fz.reference_image = image.name
    write_synced(origin / "frame_zero.json", fz.model_dump_json(indent=2).encode("utf-8"))
    timeline = building / "timelines" / ROOT_TIMELINE
    timeline.mkdir(parents=True)
    write_synced(timeline / "timeline.json", Timeline(id=ROOT_TIMELINE).model_dump_json(indent=2).encode("utf-8"))
    write_synced(building / "adventure.json",
                 Manifest(id=uuid.uuid4().hex, kind=kind).model_dump_json(indent=2).encode("utf-8"))
    if root.exists():
        root.rmdir()
    building.rename(root)
    sync_dir(root.parent)
    logger.info("A new adventure begins in %s", root)


def _finish_adoption(root: Path) -> None:
    """An adoption cut short between its two renames leaves the adventure
    only as `<root>.previous`: put it back rather than begin a new one."""
    previous = root.with_name(root.name + ".previous")
    if not (root / "adventure.json").exists() and (previous / "adventure.json").exists():
        if root.exists():
            root.rmdir()
        previous.rename(root)
        logger.warning("Restored the adventure in %s from %s (a replacement was cut short)", root, previous)


def _newest(conts: Path, *, clean: bool) -> tuple[Path, WorldModel] | None:
    """The newest complete continuation and its world. With `clean`, what a
    crash left behind goes: unfinished commits (`*.tmp`) are removed, and a
    newer continuation that does not load is set aside as `*.broken`."""
    if not conts.exists():
        return None
    if clean:
        for d in conts.glob("*.tmp"):
            shutil.rmtree(d, ignore_errors=True)
    for d in sorted((d for d in conts.iterdir() if d.is_dir() and d.name.isdigit()),
                    key=lambda d: int(d.name), reverse=True):
        try:
            return d, WorldModel.model_validate_json((d / "world.json").read_bytes())
        except (OSError, ValidationError) as exc:
            logger.warning("Continuation %s does not load (%s); using an older one", d, str(exc)[:200])
            if clean:
                d.rename(d.with_name(d.name + ".broken"))
    return None


def _reconcile(path: Path, world: WorldModel) -> None:
    """Make the event log agree with the world it resumes from: drop lines
    that do not parse or lie beyond the world's last clip (an older
    continuation was used), and add the clips the world has that the log
    lacks (a commit cut short after its rename)."""
    head = world.events[-1].segment if world.events else 0
    on_disk = [line for line in path.read_bytes().splitlines() if line.strip()] if path.exists() else []
    kept: list[EventRecord] = []
    for raw in read_jsonl(path):
        try:
            event = EventRecord.model_validate(raw)
        except ValidationError:
            continue
        if event.segment <= head:
            kept.append(event)
    if len(kept) != len(on_disk):
        logger.warning("The event log in %s disagreed with the last saved clip; repaired", path.parent)
        replace(path, "".join(e.model_dump_json() + "\n" for e in kept).encode("utf-8"))
    last = kept[-1].segment if kept else 0
    for event in world.events:
        if event.segment > last:
            append_line(path, event.model_dump_json())


def _lock(root: Path) -> None:
    """Take the adventure for this process. A lock left by a process that is
    gone, or by another machine (a copied folder), is stale and replaced."""
    path = root / ".lock"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _held(path):
                raise AdventureError(f"the adventure in {root} is open in another FFF "
                                     f"({path.read_text(errors='replace').strip()}); close it first") from None
            logger.warning("Replacing a stale lock on %s", root)
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            f.write(f"{socket.gethostname()} {os.getpid()}")
        return
    raise AdventureError(f"could not lock the adventure in {root}")


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
