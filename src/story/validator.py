"""Validate and apply a Now beat against the world model.

Plan → Diff → Validate → Apply. The LLM proposes, this module decides
what is structurally admissible, applies the admissible part, and
reports the rest so the caller can re-ask once and log the remainder.
Hard violations (unknown entities, a character the story's cast does
not allow, a line from someone who is not a character) are the ones
worth a retry; warnings are fixed up silently.

`check` looks at the whole beat before any of it plays. `apply_clip`
applies one clip when it is emitted, so a beat the viewer cuts short
leaves the world exactly as far along as the picture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models.story import Voice
from src.story.compiler import shot_before
from src.story.schemas import BeatPlan, ThenUpdate, EntityOp
from src.story.world import Entity, Era, Summary, WorldModel, slugify


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)


_LOCATION_PREFIXES = ("at ", "in ", "on ", "inside ", "the ")


def resolve_location(world: WorldModel, ref: str):
    text = " ".join(ref.split()).strip()
    candidates = [text]
    low = text.lower()
    for prefix in _LOCATION_PREFIXES:
        if low.startswith(prefix):
            candidates.append(text[len(prefix):])
    for cand in candidates:
        ent = world.resolve(cand)
        if ent is not None and ent.kind == "location":
            return ent
    padded = f"_{slugify(text)}_"
    found = [ent for ent in world.by_kind("location") if f"_{ent.id}_" in padded]
    return max(found, key=lambda e: len(e.id)) if found else None


def _check_op(op: EntityOp, world: WorldModel, introduced: set[str], r: Report, where: str) -> None:
    existing = world.resolve(op.entity)
    if op.op == "introduce":
        if existing is not None:
            r.warnings.append(f"{where}: '{op.entity}' already exists as {existing.id}; treated as update")
        elif op.kind == "character":
            cast = ", ".join(f"`{e.id}`" for e in world.by_kind("character"))
            if world.cast_locked:
                r.violations.append(
                    f"{where}: cannot introduce character '{op.entity}': this story allows no characters "
                    f"beyond its cast ({cast}); bring one of them in, or make it a prop",
                )
                return
            if not op.description.strip():
                r.violations.append(
                    f"{where}: new character '{op.entity}' needs a `description` of how it looks, "
                    "so it stays the same character in every later clip",
                )
                return
            if not op.voice.strip():
                r.warnings.append(f"{where}: new character '{op.entity}' has no `voice`; it will not sound "
                                  "the same from clip to clip")
            introduced.add(slugify(op.entity))
        else:
            introduced.add(slugify(op.entity))
    elif existing is None and slugify(op.entity) not in introduced:
        r.violations.append(
            f"{where}: {op.op} of unknown entity '{op.entity}'; use an id from "
            "'World now' or introduce it first",
        )
    if op.location:
        loc = resolve_location(world, op.location)
        if loc is None and slugify(op.location) not in introduced:
            r.warnings.append(
                f"{where}: location '{op.location}' is not a known location id; ignored "
                "(use `state` for where something sits within the location)",
            )


def check(beat: BeatPlan, world: WorldModel, *, max_clips: int = 4) -> Report:
    """Pure check of the whole beat: nothing is mutated."""
    r = Report()
    if not beat.clips:
        r.violations.append("at least one clip is required")
    elif len(beat.clips) > max_clips:
        r.warnings.append(f"{len(beat.clips)} clips given; only the first {max_clips} are used")

    introduced: set[str] = set()
    for ci, clip in enumerate(beat.clips[:max_clips], start=1):
        where = f"clip {ci}"
        for op in clip.entity_ops:
            _check_op(op, world, introduced, r, where)
        for line in clip.dialogue:
            speaker = world.resolve(line.speaker)
            if (speaker is None or speaker.kind != "character") and slugify(line.speaker) not in introduced:
                r.violations.append(f"{where}: dialogue speaker '{line.speaker}' is not a known character id")
        for ref in clip.on_screen:
            if world.resolve(ref) is None and slugify(ref) not in introduced:
                r.warnings.append(f"{where}: on_screen entity '{ref}' is unknown and was dropped")

    if beat.delivered_request and not _owed_item(world, beat.delivered_request):
        r.warnings.append(f"delivered_request {beat.delivered_request} names no deferred request; ignored")

    return r


SHORT_LOOK_WORDS = 14


def _short_look(look: str) -> str:
    text = " ".join(look.split()).rstrip(".")
    for stop in ("; ", ". "):
        cut = text.find(stop)
        if cut > 0:
            text = text[:cut]
    words = text.split()
    if len(words) > SHORT_LOOK_WORDS:
        text = " ".join(words[:SHORT_LOOK_WORDS])
        comma = text.rfind(",")
        joint = text.rfind(" and ")
        if comma > 0:
            text = text[:comma]
        elif joint > len(text) // 2:
            text = text[:joint]
    return text.rstrip(" ,;:")


def _same_shot(a: str, b: str) -> bool:
    def norm(text: str) -> str:
        words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
        return " ".join(w for w in words if w not in ("a", "an", "the"))
    return norm(a) == norm(b)


def _owed_item(world: WorldModel, segment: int):
    return next((i for i in world.interventions if i.disposition == "deferred" and i.segment == segment), None)


def apply_clip(beat: BeatPlan, index: int, world: WorldModel, *, segment_no: int) -> Report:
    """Apply clip `index` of `beat` to `world`, in place: its entity ops,
    presence from its `on_screen`, its dialogue filter. The first clip
    also lands the viewer disposition.

    Normalises the clip: `on_screen` references become entity ids,
    invalid dialogue is dropped, and a cut the story does not allow (or
    one on the story's first clip) is dropped; the world's shot follows the
    beat's framing and the clip's cut.
    """
    r = Report()
    clip = beat.clips[index]
    seg = segment_no
    before = world.current_location()

    for op in clip.entity_ops:
        ent = world.resolve(op.entity)
        if op.op == "introduce" and ent is None:
            if op.kind == "character" and (world.cast_locked or not op.description.strip()):
                continue
            eid = world._unique_id(slugify(op.entity))
            name = op.entity.strip()
            if "_" in name and " " not in name:
                name = name.replace("_", " ")
            look = " ".join(op.description.split())
            ent = Entity(
                id=eid, name=name, kind=op.kind,
                description=look, look=look if op.kind != "location" else "",
                first_seen=seg,
            )
            if ent.kind == "character":
                ent.dynamic = True
                ent.look_short = _short_look(look)
                ent.description = " ".join(op.who.split())
                ent.voice = Voice(description=" ".join(op.voice.split())) if op.voice.strip() else None
            if ent.kind == "prop":
                ent.look_short = _short_look(look)
                here = world.current_location()
                ent.location = here.id if here else ""
            ent.present = True
            world.entities[eid] = ent
            r.applied.append(f"introduce {ent.kind} {ent.id}" + (f" ({look})" if look else ""))
        if ent is None:
            continue

        if op.op == "remove":
            if not ent.gone:
                ent.gone = True
                ent.present = False
                ent.state = " ".join(op.state.split()) or "gone"
                ent.last_seen = seg
                r.applied.append(f"remove {ent.id}")
            continue

        changes: list[str] = []
        look = " ".join(op.description.split())
        if op.op == "update" and look and ent.kind != "location" and look != ent.look:
            ent.cast_look = ent.cast_look or ent.look
            ent.look = ent.look_short = look
            ent.look_since = seg
            changes.append(f"look={look}")
        state = " ".join(op.state.split())
        if state and state != ent.state:
            ent.state = state
            changes.append(f"state={state}")
        if op.location:
            loc = resolve_location(world, op.location)
            if loc is not None and loc.id != ent.location:
                ent.location = loc.id
                changes.append(f"location={loc.id}")
                world.camera_location = loc.id
                for place in world.by_kind("location"):
                    place.present = place.id == loc.id
        if op.present is not None and op.present != ent.present:
            ent.present = op.present
            changes.append(f"present={op.present}")
        ent.last_seen = seg
        if changes:
            r.applied.append(f"update {ent.id}: " + ", ".join(changes))

    kept: list[str] = []
    for ref in clip.on_screen:
        ent = world.resolve(ref)
        if ent is None or ent.gone:
            continue
        if ent.kind != "location":
            ent.present = True
        ent.last_seen = seg
        if ent.id not in kept:
            kept.append(ent.id)
    clip.on_screen = kept

    here = world.current_location()
    if here is not None:
        for eid in kept:
            if world.entities[eid].kind != "location":
                world.entities[eid].location = here.id
        if before is not None and before.id != here.id:
            left: list[str] = []
            for ent in world.entities.values():
                if ent.kind == "location" or ent.gone or not ent.present:
                    continue
                if ent.id in kept:
                    continue
                if ent.first_seen == seg:
                    ent.location = here.id
                elif ent.location != here.id:
                    ent.present = False
                    ent.location = ent.location or before.id
                    left.append(ent.id)
            r.applied.append(f"moved to {here.id}" + (f"; left behind at {before.id}: {', '.join(left)}" if left else ""))

    clip.dialogue = [
        d for d in clip.dialogue
        if (sp := world.resolve(d.speaker)) is not None and sp.kind == "character"
    ]

    cut = " ".join(clip.cut_to.split()).rstrip(".")
    if cut and not world.cuts:
        r.warnings.append(f"cut to '{cut}' dropped: this story is one continuous take")
        cut = ""
    elif cut and not world.events:
        r.warnings.append(f"cut to '{cut}' dropped: the story's first clip never cuts")
        cut = ""
    elif cut and _same_shot(cut, shot_before(beat, index)):
        r.warnings.append(f"cut to '{cut}' dropped: it is the shot the clip already opens on")
        cut = ""
    clip.cut_to = cut
    if cut:
        world.shot = cut
        r.applied.append(f"cut to {cut}")
    elif index == 0 and beat.framing.strip():
        world.shot = " ".join(beat.framing.split()).rstrip(".")

    if index == 0:
        reason = beat.intervention_reason.strip()
        pending = [i for i in world.interventions if i.disposition == "pending"]
        if pending:
            if beat.intervention == "none":
                r.warnings.append("viewer input had no disposition; recorded as deferred")
                beat.intervention = "deferred"
            for item in pending:
                item.disposition = beat.intervention
                item.reason = reason
                item.resolved_at = None if beat.intervention == "deferred" else seg
            r.applied.append(f"viewer request {beat.intervention}" + (f": {reason}" if reason else ""))
        deferred = [i for i in world.interventions if i.disposition == "deferred"]
        item = None
        disposition = "absorbed"
        if beat.delivered_request:
            item = _owed_item(world, beat.delivered_request)
            if item is None:
                r.warnings.append(f"delivered_request {beat.delivered_request} names no deferred request; ignored")
        elif not pending and beat.intervention in ("absorbed", "declined") and deferred:
            if len(deferred) == 1:
                item, disposition = deferred[0], beat.intervention
            else:
                r.warnings.append(
                    f"{beat.intervention} beat with {len(deferred)} requests owed and no delivered_request; "
                    "none resolved",
                )
        if item is not None:
            item.disposition = disposition
            item.reason = reason if not pending else (item.reason or reason)
            item.resolved_at = seg
            r.applied.append(f"deferred viewer request (segment {item.segment}) now {disposition}: {item.text!r}")

    return r


def apply_then(world: WorldModel, update: ThenUpdate, *, at_segment: int) -> list[str]:
    """Land a Then update: summaries, the rewritten notebook, and eras."""
    notes: list[str] = []

    text = update.scene_summary.strip()
    if text and at_segment > world.last_compacted:
        first = world.last_compacted + 1
        world.summaries.append(
            Summary(scope="scene", first_segment=first, last_segment=at_segment, text=text),
        )
        world.last_compacted = at_segment
        notes.append(f"compacted segments {first}-{at_segment}")
    if update.act_summary.strip():
        world.act_summary = update.act_summary.strip()
        notes.append("act summary updated")

    world.notebook = update.notebook
    notes.append("notebook updated")

    if update.era is not None:
        world.eras.append(Era(
            name=update.era.name,
            turned_at=update.era.turned_at,
            chronicle=update.era.chronicle,
        ))
        notes.append(f"era recognised: {update.era.name} (turned at segment {update.era.turned_at})")

    world.then_runs += 1
    world.last_then = at_segment
    return notes
