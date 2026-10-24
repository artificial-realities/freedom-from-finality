"""Context assembler: renders the world model into prompt sections.

Everything Now and Then read comes through here, so the prompt budget
is decided in one place: active entities in full with each character's
current state, dormant ones by name (except absent characters the story
is pointing at, recalled in full), recent clips verbatim, older history
as summaries. The system prompts (each role's craft, the
content policy, the output contract) never change during a run so the
API's prompt cache hits; everything that moves, every character
included, goes into the user prompt.
"""

from __future__ import annotations

from src.story.viewer import Answer, DirectionEnd, Said, ViewerMemory
from src.story.world import Entity, WorldModel
from src.util.prompt_loader import load_prompt, prompt_path


# ── System-prompt sections (cached; stable across a run) ───────────

def format_policy() -> str:
    """The content policy, the same in every adventure, for both system prompts."""
    return load_prompt(prompt_path("policy.md"))


# ── User-prompt sections (change every call) ───────────────────────

def format_notebook(world: WorldModel) -> str:
    """Then's notebook as Now reads it."""
    nb = world.notebook
    if not nb.characters and not nb.alive and not nb.seeds:
        return "(Then has not written the notebook yet.)"
    out: list[str] = []
    if nb.characters:
        out.append("**Characters**")
        for ch in nb.characters:
            ent = world.resolve(ch.entity)
            if ent is not None and ent.kind == "character":
                bits = _who(ent, dated=False)
            else:
                bits = [b for b in (ch.personality and f"personality: {ch.personality}",
                                    ch.lately and f"lately: {ch.lately}") if b]
            out.append(" · ".join([f"- `{ch.entity}`", *bits]))
    if nb.alive:
        out.append(f"\n**What is alive**\n{nb.alive}")
    if nb.seeds:
        out.append("\n**Seeds**")
        for s in nb.seeds:
            out.append(f"- {s.seed} → {s.potential}")
    if nb.intentions:
        out.append(f"\n**Loose intentions**\n{nb.intentions}")
    return "\n".join(out)


PRESENT_IN_FULL = 12
RECALLED_IN_FULL = 6
STATE_CHARS = 160
GONE_LISTED = 12


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _who(ent: Entity, *, dated: bool) -> list[str]:
    """Who a character is: its personality (with the development that last
    changed it, if one did) and how it has been lately."""
    bits: list[str] = []
    if ent.personality:
        changed = (f" (changed at segment {ent.personality_since}: {ent.personality_because})"
                   if ent.personality_because else "")
        bits.append(f"personality{changed}: {ent.personality}")
    if ent.lately:
        bits.append(f"lately (as of segment {ent.lately_at}): {ent.lately}" if dated else f"lately: {ent.lately}")
    return bits


def _character(ent: Entity, portrayed: set[str], lead: list[str]) -> str:
    """One character in full. Who it is comes from the notebook for the
    characters Then portrays; any other carries it here, from the entity,
    so a newcomer, or one coming back, is still who it was."""
    bits = [f"- {ent.id} (character): {ent.name}", *lead]
    if ent.look:
        bits.append(f"looks: {ent.look}")
    if ent.id not in portrayed:
        bits += _who(ent, dated=True)
    if ent.voice and ent.voice.description:
        bits.append(f"voice: {ent.voice.description}")
    return " · ".join(bits)


def recalled(world: WorldModel, viewer_input: str = "", recent: int = 6,
             viewer: ViewerMemory | None = None) -> list[Entity]:
    """Absent characters the story is pointing at: left where the picture
    is now, or named, or their place named, in what the roles read this
    call (the viewer's words and Then's understanding of them, Then's
    notebook, the characters in the scene, the recent clips). Those are
    shown in full, with the summary of when they were last seen, so a
    return is written knowing who comes back; the rest stay one line each.
    Code only matches names: which mentions matter is the roles' judgment,
    and Then keeps one in reach by naming it."""
    nb = world.notebook
    texts = [viewer_input, world.direction.text if world.direction else "", nb.alive, nb.intentions]
    understanding = viewer.understanding() if viewer is not None else None
    if understanding is not None:
        texts += [r.reading for r in understanding.readings] + [understanding.lately]
    texts += [i.text for i in world.interventions if i.disposition == "deferred"]
    texts += [f"{s.seed} {s.potential}" for s in nb.seeds]
    texts += [c.lately for c in nb.characters]
    texts += [f"{e.state} {e.lately}" for e in world.present_entities()]
    for ev in world.events[-recent:] if recent > 0 else []:
        texts += [ev.narration, ev.end_state, *ev.dialogue]
    named = world.mentions(" ".join(t for t in texts if t))
    here = world.camera_location
    found = [e for e in world.by_kind("character")
             if not e.present and not e.gone
             and ((here and e.location == here) or e.id in named or e.location in named)]
    found.sort(key=lambda e: (e.location != here, -e.last_seen))
    return found[:RECALLED_IN_FULL]


def format_world_now(world: WorldModel, recall: list[Entity] = ()) -> str:
    """The world as it is now. `recall` (from `recalled`) are the absent
    characters shown in full."""
    out: list[str] = []
    here = world.current_location()
    if here is None:
        out.append("Location: (none)")
    else:
        drawn = " ".join((here.look_short or here.look).split()).rstrip(".")
        out.append(f"Location: `{here.id}` {here.name}" + (f" · drawn as: {drawn}" if drawn else "")
                   + (f" · now: {_clip(here.state, STATE_CHARS)}" if here.state else ""))
    portrayed = {e.id for c in world.notebook.characters if (e := world.resolve(c.entity)) is not None}
    present = world.present_entities()
    if present:
        present.sort(key=lambda e: (e.kind != "character", -e.last_seen))
        full, rest = present[:PRESENT_IN_FULL], present[PRESENT_IN_FULL:]
        out.append("Present in the scene:")
        for ent in full:
            if ent.kind == "character":
                now = ent.state or "(not recorded yet: describe it in your first op)"
                out.append(_character(ent, portrayed, [f"now: {_clip(now, STATE_CHARS)}"]))
                continue
            now = f" · now: {_clip(ent.state, STATE_CHARS)}" if ent.state else ""
            looks = f" · looks: {ent.look}" if ent.look_since else ""
            out.append(f"- {ent.id} ({ent.kind}): {ent.name}{now}{looks}")
        if rest:
            out.append("Also present (seen less recently): " + ", ".join(f"`{e.id}`" for e in rest))
    else:
        out.append("Present in the scene: (nothing but the location)")
    def elsewhere(e) -> str:
        return e.location if e.location and (here is None or e.location != here.id) else ""

    def where(e) -> str:
        if here is not None and e.location == here.id:
            return "left here"
        return f"at `{e.location}`" if e.location else ""

    away = [e for e in world.by_kind("character") if not e.present and not e.gone]
    full_ids = {e.id for e in recall}
    full = [e for e in away if e.id in full_ids]
    if full:
        out.append("Characters not in the scene that the story is pointing at (named in what you read, "
                   "or left where the picture is):")
        for e in full:
            lead = [f"last seen segment {e.last_seen}", where(e)]
            if e.state:
                lead.append(f"then: {_clip(e.state, STATE_CHARS)}")
            out.append(_character(e, portrayed, [b for b in lead if b]))
    rest = [e for e in away if e.id not in full_ids]
    if rest:
        def about(e) -> str:
            bits = [f"last seen segment {e.last_seen}", e.state, where(e)]
            return " (" + ", ".join(b for b in bits if b) + ")"
        out.append(("Other characters" if full else "Characters") + " not in the scene: "
                   + "; ".join(f"`{e.id}`{about(e)}" for e in rest))
    dormant = world.dormant_props()
    if dormant:
        out.append(
            "Dormant props (introduced earlier, not in the scene; fair game for callbacks): "
            + ", ".join(f"`{e.id}` (last seen segment {e.last_seen}"
                        + (f", left at `{elsewhere(e)}`" if elsewhere(e) else "") + ")" for e in dormant)
        )
    gone = world.gone_props()
    if gone:
        shown = gone[-GONE_LISTED:]
        more = f" (+{len(gone) - len(shown)} more)" if len(gone) > len(shown) else ""
        out.append("Gone for good: " + ", ".join(f"`{e.id}`" for e in shown) + more)
    return "\n".join(out)


# ── The viewer ──────────────────────────────────────────────────────

MARKS_FOR_NOW = 5
MARKS_FOR_THEN = 10
EARLIER_LINES_FOR_THEN = 10
RECENT_MARKS = 2


def _in_play(world: WorldModel, recall: list[Entity], text: str = "") -> set[str]:
    """What the moment touches: everything in the scene, the place, the
    absent characters recalled, and whatever `text` names."""
    ids = {e.id for e in world.present_entities()} | {e.id for e in recall} | world.mentions(text)
    if world.camera_location:
        ids.add(world.camera_location)
    return ids


def _where(world: WorldModel, timeline: str, segment: int) -> str:
    where = f"segment {segment}" if segment else "before the first clip"
    return where if timeline == world.timeline else f"{where} of another timeline (`{timeline}`)"


def _evidence(world: WorldModel, lines: dict[int, Said], grounds: list[int], *, ids: bool) -> str:
    """What a reading rests on, as counted from the lines it cites."""
    cited = [lines[g] for g in grounds if g in lines]
    here = sorted(s.segment for s in cited if s.timeline == world.timeline)
    bits = [f"{len(cited)} line{'s' if len(cited) != 1 else ''}"]
    if ids:
        bits[0] += ": " + ", ".join(f"#{s.id}" for s in cited)
    if here:
        bits.append(f"segment {here[0]}" if here[0] == here[-1] else f"segments {here[0]}-{here[-1]}")
    if len(here) < len(cited):
        bits.append(f"{len(cited) - len(here)} in other timelines")
    return "; ".join(bits)


def _understanding(world: WorldModel, viewer: ViewerMemory, *, ids: bool) -> list[str]:
    u = viewer.understanding()
    if u is None:
        return []
    lines = {s.id: s for s in viewer.lines()}
    out = [f"- {r.reading} ({_evidence(world, lines, r.grounds, ids=ids)})" for r in u.readings]
    if u.lately:
        out.append(f"Lately: {u.lately}")
    if u.unknown:
        out.append(f"Unknown: {u.unknown}")
    return out


def _marks(world: WorldModel, viewer: ViewerMemory, play: set[str], cap: int, *, ids: bool,
           this_timeline: bool) -> tuple[list[str], int]:
    """The marks that touch the moment, and the most recent ones, oldest
    first; and how many others are kept but not shown."""
    marks = [m for m in viewer.marks() if m.timeline == world.timeline or not this_timeline]
    chosen = {m.id for m in marks if set(m.entities) & play} | {m.id for m in marks[-RECENT_MARKS:]}
    shown = [m for m in marks if m.id in chosen][-cap:]
    out = [f"- {m.mark} ({_where(world, m.timeline, m.segment)}"
           + (f"; from {', '.join(f'#{i}' for i in m.said)}" if ids else "") + ")" for m in shown]
    return out, len(marks) - len(shown)


def _line(world: WorldModel, viewer: ViewerMemory, said: Said, *, full: bool) -> str:
    """One of the viewer's lines with what the story did with it; `full`
    adds what was on screen when they typed it and Now's reasons."""
    head = f"- #{said.id} ({_where(world, said.timeline, said.segment)}"
    if full and said.during:
        head += f", while: {_clip(said.during, 140).rstrip('.')}"
    head += f'): "{said.text}"'
    outcomes = []
    for o in viewer.outcomes():
        if said.id not in o.said:
            continue
        if isinstance(o, Answer):
            outcomes.append(f"{o.disposition} at segment {o.segment}" + (f" ({o.reason})" if full and o.reason else ""))
        else:
            outcomes.append(f"its direction {o.how} at segment {o.segment}")
    return head + " → " + ("; ".join(outcomes) if outcomes else "not answered on screen yet")


def format_viewer(world: WorldModel, viewer: ViewerMemory, current_input: str, *, answer_clips: int = 0,
                  recall: list[Entity] = ()) -> str:
    """The viewer as Now reads it: this beat's line or their active
    direction, what is owed, Then's understanding of them, and the marks
    that touch the moment."""
    out: list[str] = []
    text = (current_input or "").strip()
    if text:
        out.append(f'Viewer input for this beat: "{text}"')
        if answer_clips:
            out.append(f"This beat answers the viewer: at most {answer_clips} clip{'s' if answer_clips != 1 else ''}. "
                       "Start the answer now; the next beat continues it.")
    else:
        out.append("No viewer input for this beat.")
        d = world.direction
        if d is not None:
            lines = {s.id: s for s in viewer.lines()}
            during = next((lines[i].during for i in d.said if i in lines and lines[i].during), "")
            age = world.segment - d.segment
            out.append(
                f'Their direction: "{d.text}", taken up at segment {d.segment} '
                f"({age} segment{'s' if age != 1 else ''} ago)"
                + (f"; they gave it while: {_clip(during, 140).rstrip('.')}" if during else "")
                + f". You took it as: {d.reason or '(no reason given)'}. "
                "Build on it while it has life; say in `direction` when it is fulfilled or has run its course."
            )
    deferred = [i for i in world.interventions if i.disposition == "deferred"]
    if deferred:
        out.append("Earlier suggestions still owed (deferred):")
        out.extend(
            f'- segment {i.segment}: "{i.text}" (deferred: {i.reason or "no reason given"})'
            for i in deferred[-4:]
        )
    resolved = [i for i in world.interventions if i.disposition in ("absorbed", "declined")]
    if resolved:
        out.append("Recently handled:")
        out.extend(f'- segment {i.segment}: "{i.text}" → {i.disposition}' for i in resolved[-3:])
    understanding = _understanding(world, viewer, ids=False)
    if understanding:
        out.append("Then's understanding of them (each reading with the lines it rests on):")
        out.extend(understanding)
    marks, _ = _marks(world, viewer, _in_play(world, recall, text), MARKS_FOR_NOW, ids=False, this_timeline=True)
    if marks:
        out.append("Their marks on this story that touch the moment (what came from them):")
        out.extend(marks)
    return "\n".join(out)


def format_viewer_then(world: WorldModel, viewer: ViewerMemory, *, upto: int, recall: list[Entity] = ()) -> str:
    """The viewer as Then reads it: its understanding as last written, the
    lines new since then (or newly answered) in full, earlier lines the
    moment or its readings touch, and the marks that touch the moment.
    `upto` is how many records the memory holds for this run; everything
    else stays in the memory, counted."""
    u = viewer.understanding()
    lines = [r for r in viewer.records[:upto] if isinstance(r, Said)]
    fresh = viewer.records[u.seen if u else 0:upto]
    new_ids = {r.id for r in fresh if isinstance(r, Said)}
    new_ids |= {i for r in fresh if isinstance(r, (Answer, DirectionEnd)) for i in r.said}
    new = [s for s in lines if s.id in new_ids]

    understanding = _understanding(world, viewer, ids=True)
    out = ["Your understanding of them as last written:" if understanding
           else "(Your understanding of them is empty so far.)"]
    out.extend(understanding)
    if new:
        out.append("Their lines new since then, with what was on screen and what the story did:")
        out.extend(_line(world, viewer, s, full=True) for s in new)
    else:
        out.append("They have said nothing new since." if lines else "They have said nothing yet.")
    play = _in_play(world, recall, " ".join(s.text for s in new)) | {e for s in new for e in s.entities}
    cited = {g for r in u.readings for g in r.grounds} if u else set()
    older = [s for s in lines if s.id not in new_ids]
    earlier = [s for s in older if s.id in cited or set(s.entities) & play][-EARLIER_LINES_FOR_THEN:]
    if earlier:
        out.append("Earlier lines this moment or your readings touch:")
        out.extend(_line(world, viewer, s, full=False) for s in earlier)
    marks, unshown = _marks(world, viewer, play, MARKS_FOR_THEN, ids=True, this_timeline=False)
    if marks:
        out.append("Their marks (what came from them):")
        out.extend(marks)
    if len(older) > len(earlier) or unshown:
        out.append(f"(Kept but not shown: {len(older) - len(earlier)} earlier lines, {unshown} marks.)")
    return "\n".join(out)


def format_summaries(world: WorldModel, scene_limit: int = 3, recall: list[Entity] = ()) -> str:
    """Act summary, the opening scene, the most recent scene summaries, and
    the scene each recalled character was last seen in."""
    out: list[str] = []
    scenes = [s for s in world.summaries if s.scope == "scene"]
    if scenes:
        first = scenes[0]
        out.append(f"Opening (segments {first.first_segment}-{first.last_segment}): {first.text}")
    shown = scenes[:1] + scenes[-scene_limit:]
    for s in scenes:
        last_here = [f"`{e.id}`" for e in recall if s.first_segment <= e.last_seen <= s.last_segment]
        if last_here and not any(s is x for x in shown):
            out.append(f"Segments {s.first_segment}-{s.last_segment} (when {', '.join(last_here)} "
                       f"{'was' if len(last_here) == 1 else 'were'} last seen): {s.text}")
    if world.act_summary:
        out.append(f"Act so far: {world.act_summary}")
    for s in scenes[-scene_limit:]:
        if s is scenes[0]:
            continue
        out.append(f"Segments {s.first_segment}-{s.last_segment}: {s.text}")
    return "\n\n".join(out) if out else "(nothing compacted yet; the recent clips are the whole story)"


def _clip_label(ev) -> str:
    beat = f" · beat {ev.beat} \"{ev.beat_intent}\" clip {ev.clip_index}/{ev.clip_count}" if ev.beat else ""
    return f"[segment {ev.segment}{beat}]"


def format_recent(world: WorldModel, count: int) -> str:
    events = world.events[-count:] if count > 0 else []
    if not events:
        return "(no clips yet; you are opening the story)"
    lines = []
    for ev in events:
        lines.append(
            f"{_clip_label(ev)} {ev.narration}\n"
            f"  on screen: {', '.join(ev.on_screen) or '(none)'} · ends on: {ev.end_state or '(unspecified)'}"
            + (" · said: " + " / ".join(ev.dialogue) if ev.dialogue else "")
        )
    return "\n\n".join(lines)


def format_turning_points(world: WorldModel, kept_from: int | None) -> str:
    """The turning points so far, and the clips a new one can be named at:
    those Then reads in full whose moment the adventure still keeps."""
    out = [f'- "{e.name}" at segment {e.turned_at}' for e in world.eras]
    out = ["So far:", *out] if out else ["None so far."]
    first = max(kept_from or 1, world.last_compacted + 1)
    if first <= world.segment:
        out.append(f"A new one can be named at segments {first}-{world.segment}: the clips whose moment "
                   "is still kept for the viewer to return to.")
    else:
        out.append("No clip can be named yet.")
    return "\n".join(out)


def format_events(world: WorldModel, since: int) -> str:
    events = world.events_since(since)
    if not events:
        return "(no events since the last compaction)"
    lines = []
    for ev in events:
        bits = [f"{_clip_label(ev)} {ev.narration}"]
        if ev.user_input:
            bits.append(f'  viewer: "{ev.user_input}" → {ev.intervention}')
        if ev.applied_ops:
            bits.append("  world: " + "; ".join(ev.applied_ops))
        if ev.dialogue:
            bits.append("  said: " + " / ".join(ev.dialogue))
        lines.append("\n".join(bits))
    return "\n\n".join(lines)
