"""Validate and apply a Director beat against the world model.

Plan → Diff → Validate → Apply. The LLM proposes, this module decides
what is structurally admissible, applies the admissible part, and
reports the rest so the caller can re-ask once and log the remainder.
Hard violations (unknown entities, a character the story's cast policy
does not allow, the protagonist removed or doubled, a perspective the
story does not permit, a mute character speaking) are the ones worth a
retry; warnings are fixed up silently.

`check` looks at the whole beat before any of it plays. `apply_clip`
applies one clip when it is emitted, so a beat the viewer cuts short
leaves the world exactly as far along as the picture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models.story import Voice
from src.story.schemas import BeatPlan, EntityOp, ShowrunnerUpdate
from src.story.world import MAX_NOTES, Entity, SceneCard, Summary, Thread, WorldModel, slugify


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    changed: bool = False


_LOCATION_PREFIXES = ("at ", "in ", "on ", "inside ", "the ")


def resolve_location(world: WorldModel, ref: str):
    """Resolve a location reference leniently: ids, names, and prefixed
    forms such as "at the_white_void". Returns an Entity or None."""
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
    for ent in world.by_kind("location"):
        if ent.id in slugify(text):
            return ent
    return None


def _doubles_protagonist(name: str, world: WorldModel) -> bool:
    """A new character whose name contains the protagonist's ("Lego Man 2",
    "another Mara"): a second copy of the one identity that must stay single."""
    hero = world.entities.get(world.protagonist)
    return hero is not None and slugify(hero.name) in slugify(name)


def _check_op(op: EntityOp, world: WorldModel, introduced: set[str], r: Report, where: str) -> None:
    existing = world.resolve(op.entity)
    if op.op == "introduce":
        if existing is not None:
            r.warnings.append(f"{where}: '{op.entity}' already exists as {existing.id}; treated as update")
        elif op.kind == "character":
            cast = ", ".join(f"`{e.id}`" for e in world.by_kind("character"))
            if not world.cast_policy.dynamic_characters:
                r.violations.append(
                    f"{where}: cannot introduce character '{op.entity}': this story allows no characters "
                    f"beyond its cast ({cast}); bring one of them in, or make it a prop",
                )
                return
            if _doubles_protagonist(op.entity, world):
                r.violations.append(
                    f"{where}: cannot introduce '{op.entity}': the protagonist is one character and "
                    "never appears twice",
                )
                return
            if not op.description.strip():
                r.violations.append(
                    f"{where}: new character '{op.entity}' needs a `description` of how it looks, "
                    "so it stays the same character in every later clip",
                )
                return
            if op.speaks and not op.voice.strip():
                r.warnings.append(f"{where}: new character '{op.entity}' speaks but has no `voice`")
            introduced.add(slugify(op.entity))
        else:
            introduced.add(slugify(op.entity))
    elif op.op == "remove" and existing is not None and existing.id == world.protagonist:
        r.violations.append(f"{where}: the protagonist `{existing.id}` cannot be removed from the story")
    elif existing is None and slugify(op.entity) not in introduced:
        r.violations.append(
            f"{where}: {op.op} of unknown entity '{op.entity}'; use an id from "
            "'World now' or introduce it first",
        )
    if op.holder and op.holder.strip().lower() != "nobody":
        holder = world.resolve(op.holder)
        if holder is None or holder.kind != "character":
            r.violations.append(f"{where}: holder '{op.holder}' is not a known character id")
        elif existing is not None and existing.kind == "character":
            r.warnings.append(f"{where}: a character cannot be held; holder on {existing.id} ignored")
    if op.location:
        loc = resolve_location(world, op.location)
        if loc is None and slugify(op.location) not in introduced:
            r.warnings.append(
                f"{where}: location '{op.location}' is not a known location id; ignored "
                "(use `status` for where something sits within the location)",
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
            if speaker is None or speaker.kind != "character":
                r.violations.append(f"{where}: dialogue speaker '{line.speaker}' is not a known character id")
            elif not speaker.speaks:
                r.violations.append(f"{where}: {speaker.name} never speaks in this world; remove the dialogue line")
        for ref in clip.on_screen:
            if world.resolve(ref) is None and slugify(ref) not in introduced:
                r.warnings.append(f"{where}: on_screen entity '{ref}' is unknown and was dropped")

    if beat.delivered_request and not _owed_item(world, beat.delivered_request):
        r.warnings.append(f"delivered_request {beat.delivered_request} names no deferred request; ignored")

    if not world.perspective.permits(beat.perspective):
        allowed = ", ".join([world.perspective.default, *world.perspective.allowed])
        r.violations.append(
            f"perspective '{beat.perspective}' is not one this story allows ({allowed}); "
            "leave `perspective` empty for the default",
        )

    open_ids = {t.id for t in world.open_threads()}
    for top in beat.thread_ops:
        if top.op != "open" and top.thread not in open_ids:
            r.warnings.append(f"thread op '{top.op}' on unknown or closed thread '{top.thread}' ignored")
    return r


SHORT_LOOK_WORDS = 14


def _short_look(look: str) -> str:
    """A character introduced by the film gets its look cut to its first
    clause (at most SHORT_LOOK_WORDS words) as the short look later clips
    carry, so its identity is restated on every clip it is in."""
    text = " ".join(look.split()).rstrip(".")
    for stop in ("; ", ". "):
        cut = text.find(stop)
        if cut > 0:
            text = text[:cut]
    words = text.split()
    return " ".join(words[:SHORT_LOOK_WORDS]).rstrip(" ,;:")


def _owed_item(world: WorldModel, segment: int):
    """The deferred request recorded at `segment`: the id the Director
    reads in "Earlier requests still owed" and names in
    `delivered_request`. One plan records at most one line per segment
    (the live loop joins lines typed together), so the segment is unique."""
    return next((i for i in world.interventions if i.disposition == "deferred" and i.segment == segment), None)


def apply_clip(beat: BeatPlan, index: int, world: WorldModel, *, segment_no: int) -> Report:
    """Apply clip `index` of `beat` to `world`, in place: its entity ops,
    presence from its `on_screen`, its dialogue filter. The first clip
    also lands the beat's thread ops and the viewer disposition.

    Normalises the clip: `on_screen` references become entity ids and
    invalid dialogue is dropped.
    """
    r = Report()
    clip = beat.clips[index]
    seg = segment_no

    for op in clip.entity_ops:
        ent = world.resolve(op.entity)
        if op.op == "introduce" and ent is None:
            if op.kind == "character" and (world.cast_locked or not op.description.strip()
                                           or _doubles_protagonist(op.entity, world)):
                continue    # reported as a violation by check()
            eid = world._unique_id(slugify(op.entity))
            name = op.entity.strip()
            if "_" in name and " " not in name:
                name = name.replace("_", " ")  # the model sent an id-like name
            ent = Entity(
                id=eid, name=name, kind=op.kind,
                description=op.description.strip(),
                look=op.description.strip() if op.kind != "location" else "",
                first_seen=seg,
            )
            if ent.kind == "character":
                # Registered once, with what keeps it the same character:
                # its look (and a short form for later clips), role, voice,
                # whether it speaks, and its state from this op.
                ent.dynamic = True
                ent.look_short = _short_look(ent.look)
                ent.role = " ".join(op.role.split())
                ent.speaks = bool(op.speaks)
                ent.voice = Voice(description=" ".join(op.voice.split())) if op.voice.strip() else None
            if ent.kind == "prop":
                here = world.current_location()
                ent.location = here.id if here else ""
            ent.present = True
            world.entities[eid] = ent
            r.applied.append(
                f"introduce {ent.kind} {ent.id}" + (f" ({ent.description})" if ent.description else ""),
            )
            r.changed = True
        if ent is None:
            continue  # reported as a violation by check()

        if op.op == "remove":
            if ent.id == world.protagonist:
                continue    # reported as a violation by check()
            if not ent.gone:
                ent.gone = True
                ent.present = False
                ent.holder = ""
                ent.status = op.status.strip() or "gone"
                ent.last_seen = seg
                r.applied.append(f"remove {ent.id}")
                r.changed = True
            continue

        changes: list[str] = []
        status = op.status.strip()
        if status and status != ent.status:
            ent.status = status
            changes.append(f"status={status}")
        for attr in ("posture", "activity", "position"):
            value = " ".join(getattr(op, attr).split())
            if value and value != getattr(ent, attr):
                if ent.kind != "character":
                    r.warnings.append(f"{attr} on {ent.id} ignored: not a character")
                    continue
                setattr(ent, attr, value)
                changes.append(f"{attr}={value}")
        if op.holder:
            if ent.kind == "character":
                r.warnings.append(f"holder on character {ent.id} ignored")
            else:
                if op.holder.strip().lower() == "nobody":
                    new_holder = ""
                else:
                    holder = world.resolve(op.holder)
                    new_holder = (
                        holder.id if holder is not None and holder.kind == "character"
                        else ent.holder
                    )
                if new_holder != ent.holder:
                    ent.holder = new_holder
                    changes.append(f"holder={new_holder or 'nobody'}")
                    r.changed = True
        if op.location:
            loc = resolve_location(world, op.location)
            if loc is not None and loc.id != ent.location:
                ent.location = loc.id
                changes.append(f"location={loc.id}")
                r.changed = True
                if ent.id == world.protagonist:
                    for place in world.by_kind("location"):
                        place.present = place.id == loc.id
        if op.present is not None and op.present != ent.present:
            ent.present = op.present
            changes.append(f"present={op.present}")
            r.changed = True
            if not op.present and ent.holder:
                ent.holder = ""
        ent.last_seen = seg
        if changes:
            r.applied.append(f"update {ent.id}: " + ", ".join(changes))

    # Presence from the clip; references normalised to ids.
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

    clip.dialogue = [
        d for d in clip.dialogue
        if (sp := world.resolve(d.speaker)) is not None and sp.kind == "character" and sp.speaks
    ]

    if index == 0:
        for top in beat.thread_ops:
            if top.op == "open":
                text = top.thread.strip()
                if not text:
                    continue
                thread = Thread(id=world.new_thread_id(), text=text, kind=top.kind, opened_at=seg)
                world.threads.append(thread)
                r.applied.append(f"thread {thread.id} opened: {thread.text}")
                r.changed = True
            else:
                thread = next((t for t in world.threads if t.id == top.thread and t.status == "open"), None)
                if thread is None:
                    continue
                thread.status = "paid_off" if top.op == "pay_off" else "dropped"
                thread.closed_at = seg
                r.applied.append(f"thread {thread.id} {thread.status}: {thread.text}")
                if top.op == "pay_off":
                    r.changed = True

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
        # A deferred request is resolved by identity (ARCH-07): the one the
        # beat names in `delivered_request`. Without the field, an absorbed
        # or declined beat with no new line still resolves the only one
        # owed; with two or more owed nothing is guessed.
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

    if r.changed:
        world.last_change = seg
    return r


_NOTE_STOPWORDS = frozenset(
    "with that this from have been their they them into onto only just also very most more "
    "some such than then there here what when where which while your about after before over under".split()
)
# A note whose significant words are at least this share of the cast
# description's (with at least `_NOTE_MIN_WORDS` of them) restates it.
_NOTE_RESTATES = 0.75
_NOTE_MIN_WORDS = 4


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _significant(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) >= 4 and w not in _NOTE_STOPWORDS]


def redundant_note(body: str, ent: Entity, world: WorldModel, update: ShowrunnerUpdate) -> str | None:
    """Why a Showrunner note adds nothing, or None: it is already a note,
    it is contained in a summary (the stored ones or this update's), or it
    restates the entity's cast description (contained in it, or three
    quarters of its words drawn from it)."""
    note = _norm(body)
    if not note:
        return "empty"
    if any(note == _norm(n) for n in ent.notes):
        return "already noted"
    sheet = " ".join((ent.description, ent.look, ent.look_short, ent.reference_pose))
    if note in _norm(sheet):
        return "contained in the cast description"
    words = _significant(body)
    if len(words) >= _NOTE_MIN_WORDS:
        known = set(_significant(sheet))
        if sum(w in known for w in words) / len(words) >= _NOTE_RESTATES:
            return "restates the cast description"
    summaries = [s.text for s in world.summaries] + [world.act_summary, update.scene_summary, update.act_summary]
    if any(note in _norm(text) for text in summaries if text):
        return "contained in a summary"
    return None


def apply_showrunner(
    world: WorldModel, update: ShowrunnerUpdate, *, at_segment: int, force_replan: bool,
) -> list[str]:
    """Land a Showrunner update: summaries, notes, pruning, plan."""
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

    # Notes: characters only (nothing shows a prop's notes), never a
    # restatement of the cast sheet or a summary, at most MAX_NOTES each.
    for note in update.entity_notes:
        ent = world.resolve(note.entity)
        body = " ".join(note.note.split())
        if ent is None or not body:
            continue
        if ent.kind != "character":
            notes.append(f"note on {ent.id} not kept (only characters keep notes): {body}")
            continue
        why = redundant_note(body, ent, world, update)
        if why:
            notes.append(f"note on {ent.id} not kept ({why}): {body}")
            continue
        ent.notes.append(body)
        notes.append(f"note on {ent.id}: {body}")
        if len(ent.notes) > MAX_NOTES:
            dropped = ent.notes[: len(ent.notes) - MAX_NOTES]
            del ent.notes[: len(dropped)]
            notes.append(f"oldest note on {ent.id} dropped (cap {MAX_NOTES}): " + " | ".join(dropped))

    for tid in update.threads_to_drop:
        thread = next((t for t in world.threads if t.id == tid and t.status == "open"), None)
        if thread is not None:
            thread.status = "dropped"
            thread.closed_at = at_segment
            notes.append(f"thread {tid} dropped")

    arc = [a.strip() for a in update.arc if a.strip()]
    if arc:
        world.plan.arc = arc
        world.plan.current_act = min(max(update.current_act, 0), len(arc) - 1)

    if force_replan or update.replan or world.plan.scene is None:
        prev = world.plan.scene
        card = SceneCard(
            id=(prev.id + 1) if prev else 1,
            goal=update.next_scene.goal.strip(),
            tension=update.next_scene.tension.strip(),
            exit_condition=update.next_scene.exit_condition.strip(),
            intensity=update.next_scene.intensity,
            candidate_beats=[b.strip() for b in update.next_scene.candidate_beats if b.strip()],
            opened_at=at_segment + 1,
        )
        world.plan.scene = card
        world.scene_started = at_segment + 1
        world.last_change = max(world.last_change, at_segment)
        world.viewer_momentum = ""
        world.viewer_momentum_segment = 0
        notes.append(f"scene {card.id} opened: {card.goal}")

    world.showrunner_runs += 1
    world.last_showrunner = at_segment
    return notes
