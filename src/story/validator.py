"""Validate and apply a Now beat against the world model.

Plan → Diff → Validate → Apply. The LLM proposes, this module decides
what is structurally admissible, applies the admissible part, and
reports the rest so the caller can re-ask once and log the remainder.
Hard violations (unknown entities, a new character without a look, a
line from someone who is not a character) are the ones worth a retry;
warnings are fixed up silently.

An entity that left the story (`gone`) stays in the world: a name it had
introduced again is someone new, and an update that puts it back in the
scene brings it back.

`check` looks at the whole beat before any of it plays. `apply_clip`
applies one clip when it is emitted, so a beat the viewer cuts short
leaves the world exactly as far along as the picture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.story.compiler import shot_before
from src.story.schemas import BeatPlan, EntityOp, ThenUpdate
from src.story.history import Checkpoint, History, Summary
from src.story.world import Direction, Entity, OpenInput, WorldModel, short_look, slugify


@dataclass
class Report:
    """What a check or an apply found. `outcomes` are what became of the
    viewer's inputs in the clip (the ids of the inputs, the outcome, why),
    for the viewer's memory once the clip airs."""

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    outcomes: list[tuple[list[int], str, str]] = field(default_factory=list)


_LOCATION_PREFIXES = ("at ", "in ", "on ", "inside ", "the ")
# The notebook's seeds, as Then is told: the ones that matter most.
SEEDS_KEPT = 8


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
        if existing is not None and not existing.gone:
            r.warnings.append(f"{where}: '{op.entity}' already exists as {existing.id}; treated as update")
        elif op.kind == "character":
            if not op.look.strip():
                r.violations.append(
                    f"{where}: new character '{op.entity}' needs a `look`, how it looks, "
                    "so it stays the same character in every later clip",
                )
                return
            if existing is not None:
                r.warnings.append(f"{where}: '{op.entity}' is someone new: the {existing.name} of clip "
                                  f"{existing.last_seen} ({existing.id}) left the story")
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
    elif existing is not None and existing.gone and op.op == "update" and op.present is not True:
        r.warnings.append(f"{where}: {existing.id} left the story; an update brings it back only with "
                          "`present: true`")
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
        back = {world.resolve(op.entity).id for op in clip.entity_ops
                if op.op == "update" and op.present is True and world.resolve(op.entity) is not None}
        for ref in clip.on_screen:
            ent = world.resolve(ref)
            if ent is None and slugify(ref) not in introduced:
                r.warnings.append(f"{where}: on_screen entity '{ref}' is unknown and was dropped")
            elif ent is not None and ent.gone and ent.id not in back and slugify(ref) not in introduced:
                r.warnings.append(f"{where}: on_screen entity '{ref}' left the story and was dropped; "
                                  "an update with `present: true` brings it back")

    for o in beat.owed:
        if _owed_item(world.owed(), o.input) is None:
            r.warnings.append(f"owed input #{o.input} is not owed; ignored")

    return r


def _same_shot(a: str, b: str) -> bool:
    def norm(text: str) -> str:
        words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
        return " ".join(w for w in words if w not in ("a", "an", "the"))
    return norm(a) == norm(b)


def _owed_item(owed: list[OpenInput], input_id: int) -> OpenInput | None:
    return next((i for i in owed if input_id in i.inputs), None)


def _cut_lands(world: WorldModel, refs: list[str], cut: str, clip_no: int):
    """Where a cut lands when no op moved the picture: nowhere new while
    anything in the shot is already here (it is a new view of this place);
    otherwise the known location the cut names, or else the one location
    everything in the shot was last at. Things introduced in this clip
    carry no evidence of where they are."""
    here = world.camera_location
    places: set[str] = set()
    for ref in refs:
        ent = world.resolve(ref)
        if ent is None or ent.gone or ent.first_seen == clip_no:
            continue
        place = ent.id if ent.kind == "location" else ent.location
        if place == here:
            return None
        if place:
            places.add(place)
    named = resolve_location(world, cut)
    if named is not None:
        return named if named.id != here else None
    if len(places) == 1:
        return world.entities.get(places.pop())
    return None


def _alias(beat: BeatPlan, index: int, introduce: EntityOp, gone_id: str, new_id: str) -> None:
    """A gone name introduced again: in the rest of the beat, the name and
    the gone one's id mean the new one, since Now wrote them before it
    could know the new id."""
    names = {slugify(introduce.entity), introduce.entity.strip().lower(), gone_id}

    def means(ref: str) -> bool:
        return ref.strip().lower() in names or slugify(ref) in names

    for clip in beat.clips[index:]:
        clip.on_screen = [new_id if means(ref) else ref for ref in clip.on_screen]
        for line in clip.dialogue:
            if means(line.speaker):
                line.speaker = new_id
        for op in clip.entity_ops:
            if op is not introduce and op.op != "introduce" and means(op.entity):
                op.entity = new_id


def apply_clip(beat: BeatPlan, index: int, world: WorldModel, *, clip_no: int) -> Report:
    """Apply clip `index` of `beat` to `world`, in place: its entity ops,
    presence from its `on_screen`, its dialogue filter. The first clip
    also lands how the beat takes the viewer's input (one it absorbs
    becomes the direction), resolves the owed inputs Now names, and ends
    the viewer's direction when Now says it is fulfilled or has run its
    course.

    Normalises the clip: `on_screen` references become entity ids,
    invalid dialogue is dropped, and a cut on the story's first clip (or
    to the shot it already opens on) is dropped; the world's shot follows
    the beat's framing and the clip's cut, and the picture's location
    follows an op or, failing one, where the cut evidently lands.
    """
    r = Report()
    clip = beat.clips[index]
    before = world.current_location()
    shown = {e.id for ref in clip.on_screen if (e := world.resolve(ref)) is not None}

    for op in clip.entity_ops:
        ent = world.resolve(op.entity)
        if op.op == "introduce" and (ent is None or ent.gone):
            if op.kind == "character" and not op.look.strip():
                continue
            namesake = ent
            eid = world._unique_id(slugify(op.entity))
            if namesake is not None:
                r.applied.append(f"{op.entity.strip()} is someone new ({eid}); {namesake.id} left the story "
                                 f"at clip {namesake.last_seen}")
                _alias(beat, index, op, namesake.id, eid)
            name = op.entity.strip()
            if "_" in name and " " not in name:
                name = name.replace("_", " ")
            look = " ".join(op.look.split())
            # A new place keeps its look too: the video model draws it from that.
            ent = Entity(
                id=eid, name=name, kind=op.kind,
                look=look, look_short=short_look(look),
                first_seen=clip_no,
            )
            if ent.kind == "character":
                ent.personality = " ".join(op.personality.split())
                ent.voice = " ".join(op.voice.split())
            if ent.kind == "prop":
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
                ent.last_seen = clip_no
                r.applied.append(f"remove {ent.id}")
            continue

        changes: list[str] = []
        if ent.gone:
            if op.present is not True:
                continue
            # Back in the story: the update that puts it in the scene.
            ent.gone = False
            changes.append("back in the story")
        was_present = ent.present
        name = " ".join(op.name.split())
        if op.op == "update" and name and name != ent.name:
            if ent.name not in ent.names:
                ent.names.append(ent.name)
            ent.name = name
            changes.append(f"name={name}")
        look = " ".join(op.look.split())
        if op.op == "update" and look and look != ent.look:
            # The new look is restated whole on every clip, not cut short:
            # a costume change has to win over the picture, and a short look
            # cut from the new one lets the old appearance back. With the
            # whole new look on every clip a helmet stayed on in 20 of 22
            # (decisions.md, "A costume changes the wearer's look").
            ent.look = ent.look_short = look
            ent.look_since = clip_no
            changes.append(f"look={look}")
        # A character may get its voice when it first speaks, or a new one
        # when its voice changes for good.
        voice = " ".join(op.voice.split())
        if op.op == "update" and voice and ent.kind == "character" and voice != ent.voice:
            ent.voice = voice
            changes.append(f"voice={voice}")
        state = " ".join(op.state.split())
        if state and state != ent.state:
            ent.state = state
            changes.append(f"state={state}")
        if op.location:
            loc = resolve_location(world, op.location)
            if loc is not None:
                if loc.id != ent.location:
                    ent.location = loc.id
                    changes.append(f"location={loc.id}")
                # The camera goes where something in the picture goes. One
                # leaving the picture, or out of it already, goes there alone
                # and the camera stays; coming back to one left elsewhere is
                # going to its place, even though its location is unchanged.
                in_picture = op.present if op.present is not None else (ent.present or ent.id in shown)
                if in_picture and loc.id != world.camera_location:
                    world.camera_location = loc.id
                    for place in world.by_kind("location"):
                        place.present = place.id == loc.id
        if op.present is not None and op.present != ent.present:
            ent.present = op.present
            changes.append(f"present={op.present}")
        if was_present or ent.present:
            ent.last_seen = clip_no
        if changes:
            r.applied.append(f"update {ent.id}: " + ", ".join(changes))

    cut = " ".join(clip.cut_to.split()).rstrip(".")
    if cut and not world.clips:
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

    # A cut can go back to someone or something left at another location
    # without an op saying so. When no op moved the picture, the cut lands
    # where its shot evidently is (see `_cut_lands`), and everything the
    # clip shows is there, as with an op.
    if cut and before is not None and world.camera_location == before.id:
        dest = _cut_lands(world, clip.on_screen, cut, clip_no)
        if dest is not None:
            world.camera_location = dest.id
            for place in world.by_kind("location"):
                place.present = place.id == dest.id
            r.applied.append(f"the cut lands in {dest.id}")

    kept: list[str] = []
    for ref in clip.on_screen:
        ent = world.resolve(ref)
        if ent is None or ent.gone:
            continue
        if ent.kind != "location":
            ent.present = True
        ent.last_seen = clip_no
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
                if ent.first_seen == clip_no:
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

    if index == 0:
        if world.direction is not None and beat.direction in ("fulfilled", "ran its course"):
            r.applied.append(f"viewer direction {beat.direction}: {world.direction.text!r}")
            r.outcomes.append((world.direction.inputs, beat.direction, ""))
            world.direction = None
        reason = beat.input_reason.strip()
        owed = world.owed()
        pending = [i for i in world.inputs if not i.owed]
        if pending:
            if beat.input == "none":
                r.warnings.append("viewer input had no outcome; recorded as deferred")
                beat.input = "deferred"
            for item in pending:
                r.outcomes.append((item.inputs, beat.input, reason))
                if beat.input == "deferred":
                    item.owed, item.reason = True, reason
                else:
                    world.inputs.remove(item)
            if beat.input == "absorbed":
                if world.direction is not None:
                    r.outcomes.append((world.direction.inputs, "superseded", ""))
                world.direction = Direction(text=" ".join(i.text for i in pending), clip=clip_no,
                                            inputs=[n for i in pending for n in i.inputs], reason=reason)
            r.applied.append(f"viewer input {beat.input}" + (f": {reason}" if reason else ""))
        # Owed inputs Now resolves by id: delivered (the first becomes the
        # direction, unless this beat took up a new input) or let go.
        # Nothing resolves an owed input but Now saying so.
        directed = bool(pending) and beat.input == "absorbed"
        for o in beat.owed:
            item = _owed_item(owed, o.input)
            if item is None or item not in world.inputs:
                r.warnings.append(f"owed input #{o.input} is not owed; ignored")
                continue
            why = " ".join(o.reason.split())
            world.inputs.remove(item)
            r.outcomes.append((item.inputs, o.outcome, why))
            r.applied.append(f"owed input #{item.id} (clip {item.clip}) {o.outcome}: {item.text!r}"
                             + (f" ({why})" if why else ""))
            if o.outcome == "delivered" and not directed:
                if world.direction is not None:
                    r.outcomes.append((world.direction.inputs, "superseded", ""))
                world.direction = Direction(text=item.text, clip=clip_no, inputs=list(item.inputs), reason=why)
                directed = True

    return r


def involved(world: WorldModel, first: int, last: int) -> set[str]:
    """The entities clips `first`-`last` involve: who and what was on
    screen, the places the picture was in, what entered, and who or what
    their action and lines name."""
    ids: set[str] = set()
    for c in world.clips:
        if first <= c.clip <= last:
            ids |= set(c.on_screen)
            if c.location:
                ids.add(c.location)
            ids |= world.mentions(" ".join([c.action, c.end_state, *c.dialogue]))
    ids |= {e.id for e in world.entities.values() if first <= e.first_seen <= last}
    return ids


def apply_then(world: WorldModel, history: History, update: ThenUpdate, *, at_clip: int) -> list[str]:
    """Land a Then update: its summary and checkpoint into the story's
    history, each with the entities it involves; the story so far and
    since the last checkpoint; the rewritten notebook. Each portrait also
    lands on its character, so who a character is survives the notebook
    dropping it. An empty personality keeps the one the character has; a
    new one replaces it only with a `because` (what in the story changed
    it), so a core change is a recorded development, never a rewording. A
    first personality needs none.

    A checkpoint names a clip after the last checkpoint, up to the one
    Then read up to (`at_clip`). With it, the story so far is replaced
    when Then rewrote it (else the old one stands, up to the checkpoint it
    was written at, and the roles read the checkpoints after it), and
    since the last checkpoint starts again from what Then wrote."""
    notes: list[str] = []
    history.truncate(world.history_count)

    text = " ".join(update.summary.split())
    if text and at_clip > world.last_summary:
        first = world.last_summary + 1
        history.records.append(Summary(first_clip=first, last_clip=at_clip, text=text,
                                       entities=sorted(involved(world, first, at_clip))))
        world.last_summary = at_clip
        notes.append(f"summarised clips {first}-{at_clip}")

    cp = update.checkpoint
    name = " ".join(cp.name.split()) if cp is not None else ""
    turned = False
    if cp is not None and name:
        last = history.last_checkpoint()
        after = last.at if last is not None else 0
        if not after < cp.at <= at_clip:
            notes.append(f"checkpoint {name!r} not kept: clip {cp.at} is not after the last checkpoint "
                         f"(clip {after}) and up to clip {at_clip}")
        else:
            first = after + 1
            ids = involved(world, first, cp.at) | world.mentions(cp.summary)
            for s in history.summaries():
                if s.last_clip >= first and s.first_clip <= cp.at:
                    ids |= set(s.entities)
            history.records.append(Checkpoint(name=name, first_clip=first, at=cp.at,
                                              summary=" ".join(cp.summary.split()), noticed_at=at_clip,
                                              entities=sorted(ids)))
            turned = True
            notes.append(f"checkpoint: {name} (clips {first}-{cp.at})")
            so_far = " ".join(update.story_so_far.split())
            if so_far:
                world.story_so_far, world.story_so_far_at = so_far, cp.at
                notes.append("the story so far rewritten")
            else:
                notes.append("the story so far kept: the checkpoint came without it")
    since = " ".join(update.since_checkpoint.split())
    if since or turned:
        world.since_checkpoint = since
        notes.append("since the last checkpoint " + ("rewritten" if since else "begins again"))
    world.history_count = len(history.records)

    # A copy, so the raw update recorded in then.jsonl stays as Then wrote it.
    world.notebook = update.notebook.model_copy(deep=True)
    notes.append("notebook updated")
    if len(world.notebook.seeds) > SEEDS_KEPT:
        notes.append(f"{len(world.notebook.seeds)} seeds; the first {SEEDS_KEPT} kept")
        del world.notebook.seeds[SEEDS_KEPT:]
    for portrait in world.notebook.characters:
        ent = world.resolve(portrait.entity)
        if ent is None or ent.kind != "character":
            continue
        portrait.entity = ent.id
        personality = " ".join(portrait.personality.split())
        because = " ".join(portrait.because.split())
        if personality and personality != ent.personality:
            if ent.personality and not because:
                notes.append(f"personality of {ent.id} kept: a new one came without `because`")
            else:
                ent.personality = personality
                ent.personality_since, ent.personality_because = at_clip, because
                notes.append(f"personality of {ent.id} rewritten: {personality}"
                             + (f" (because: {because})" if because else ""))
        portrait.personality = ent.personality
        ent.lately = " ".join(portrait.lately.split())
        ent.lately_at = at_clip

    world.then_runs += 1
    world.last_then = at_clip
    return notes
