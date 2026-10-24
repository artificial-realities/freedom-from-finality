"""The story map: the adventure as the viewer has lived it.

Read from the adventure each time it is asked for and never stored, so it
cannot drift from the story. It holds the frame zero as the origin; every
timeline, where it began and where it stands now (its latest position);
the story checkpoints along each (Then's turning points, with the era,
chronicle, place, who was there and a still); the turning points that
could not be kept (landmarks, seen but not returned to); the stretches of
each timeline's story as Then summarised them; and the places the
picture passed through. The origin and the checkpoints are the points
the story can go on from, each timeline's latest position the one it
resumes. A simple timeline draws it today; an illustrated map can draw
the same data, with illustrations of its own kept apart from the
checkpoints they are made from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.adventure import ORIGIN, Adventure, Kind
from src.story.world import EventRecord, WorldModel


class Point(BaseModel):
    """A moment the story can go on from in a new timeline: the origin (the
    frame zero) or a story checkpoint, "<timeline>/<segment>". `still` is
    the address of its picture ("" when it has none)."""

    id: str
    timeline: str = ""
    segment: int = 0
    name: str
    chronicle: str = ""
    place: str = ""
    on_screen: list[str] = Field(default_factory=list)
    picture: str = ""
    still: str = ""
    recognised_at: int = 0
    created_at: str = ""


class Landmark(BaseModel):
    """A turning point Then named whose moment was not kept: it marks the
    map, and cannot be returned to."""

    segment: int
    name: str
    chronicle: str = ""
    recognised_at: int = 0


class Stretch(BaseModel):
    """What happened over segments `first`-`last`, as Then summarised it."""

    first: int
    last: int
    text: str


class Visit(BaseModel):
    """A run of clips the picture spent in one place."""

    place: str
    first: int
    last: int


class Position(BaseModel):
    """Where a timeline stands: the last clip the viewer saw in it (segment
    0: nothing yet, the first frame)."""

    segment: int = 0
    place: str = ""
    on_screen: list[str] = Field(default_factory=list)
    picture: str = ""
    still: str = ""


class Line(BaseModel):
    """One timeline: the point it `begins` from (the origin, or the
    checkpoint of `parent` at `forked_at`), its own clips, checkpoints,
    landmarks, stretches and visits, and where it stands (`head`)."""

    id: str
    parent: str | None = None
    begins: str = ORIGIN
    forked_at: int = 0
    created_at: str = ""
    clips: int = 0
    head: Position = Field(default_factory=Position)
    checkpoints: list[str] = Field(default_factory=list)
    landmarks: list[Landmark] = Field(default_factory=list)
    stretches: list[Stretch] = Field(default_factory=list)
    visits: list[Visit] = Field(default_factory=list)
    act: str = ""


class StoryMap(BaseModel):
    """The whole map. `current` is the timeline the viewer last played,
    `playing` whether a film is on now, `clip_seconds` how long a segment
    is."""

    adventure: str
    kind: Kind
    origin: Point
    points: list[Point] = Field(default_factory=list)
    timelines: list[Line] = Field(default_factory=list)
    current: str
    playing: bool = False
    clip_seconds: float = 6.0


def build(adventure: Adventure, *, playing: bool = False, clip_seconds: float = 6.0) -> StoryMap:
    fz = adventure.origin
    origin = Point(id=ORIGIN, name="Frame zero", picture=" ".join(fz.scene.split()),
                   place=fz.location.name, on_screen=[fz.character.name],
                   still="/still/origin" if adventure.origin_image() is not None else "")
    points: list[Point] = []
    lines: list[Line] = []
    for t in adventure.timelines():
        line = Line(id=t.id, parent=t.parent, forked_at=t.forked_at or 0, created_at=t.created_at,
                    begins=f"{t.parent}/{t.forked_at}" if t.parent else ORIGIN)
        events = adventure.events(t.id)
        head = adventure.head(t.id)
        line.clips = len(events)
        line.visits = _visits(events, head[1] if head is not None else None)
        kept = set()
        for folder, cp in adventure.checkpoints(t.id):
            pid = f"{t.id}/{cp.segment}"
            points.append(Point(
                id=pid, timeline=t.id, segment=cp.segment, name=cp.name, chronicle=cp.chronicle,
                place=cp.place, on_screen=cp.on_screen, picture=cp.picture, recognised_at=cp.recognised_at,
                created_at=cp.created_at, still=f"/still/{pid}" if (folder / "still.png").exists() else "",
            ))
            line.checkpoints.append(pid)
            kept.add(cp.segment)
        if head is not None:
            cont, world = head
            line.head = _position(world, events)
            if (cont / "window.mp4").exists():
                line.head.still = f"/still/head/{t.id}"
            elif not line.head.segment:
                line.head.picture, line.head.still = origin.picture, origin.still
            line.landmarks = [Landmark(segment=e.turned_at, name=e.name, chronicle=e.chronicle,
                                       recognised_at=e.recognised_at)
                              for e in world.eras if e.timeline == t.id and e.turned_at not in kept]
            line.stretches = [Stretch(first=s.first_segment, last=s.last_segment, text=s.text)
                              for s in world.summaries if s.scope == "scene" and s.last_segment > line.forked_at]
            line.act = world.act_summary
        elif t.parent is None:
            line.head = Position(place=fz.location.name, on_screen=[fz.character.name],
                                 picture=origin.picture, still=origin.still)
        lines.append(line)
    return StoryMap(adventure=adventure.id, kind=adventure.manifest.kind, origin=origin, points=points,
                    timelines=lines, current=adventure.manifest.timeline, playing=playing,
                    clip_seconds=clip_seconds)


def _position(world: WorldModel, events: list[EventRecord]) -> Position:
    """Where `world` stands: its last clip, named as the viewer would."""
    ev = world.events[-1] if world.events else (events[-1] if events else None)
    if ev is None:
        here = world.current_location()
        return Position(place=here.name if here else "")
    place = world.entities.get(ev.location)
    return Position(segment=ev.segment, place=place.name if place else "", picture=ev.narration,
                    on_screen=[world.entities[i].name for i in ev.on_screen if i in world.entities])


def _visits(events: list[EventRecord], world: WorldModel | None) -> list[Visit]:
    """The places the picture passed through, run by run, named from the
    latest world (a place keeps its name)."""
    names = {e.id: e.name for e in world.entities.values()} if world is not None else {}
    out: list[Visit] = []
    for ev in events:
        name = names.get(ev.location, ev.location)
        if out and out[-1].place == name:
            out[-1].last = ev.segment
        else:
            out.append(Visit(place=name, first=ev.segment, last=ev.segment))
    return out
