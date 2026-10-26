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

from src.story.viewer import Input, Outcome, ViewerMemory
from src.story.world import Entity, WorldModel
from src.util.prompt_loader import load_prompt, prompt_path


# ── System-prompt sections (cached; stable across a run) ───────────

def format_policy() -> str:
    """The content policy, the same in every story, for both system prompts."""
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
        changed = (f" (changed at clip {ent.personality_since}: {ent.personality_because})"
                   if ent.personality_because else "")
        bits.append(f"personality{changed}: {ent.personality}")
    if ent.lately:
        bits.append(f"lately (as of clip {ent.lately_at}): {ent.lately}" if dated else f"lately: {ent.lately}")
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
    call (the viewer's input and Then's understanding of them, Then's
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
    texts += [i.text for i in world.owed()]
    texts += [f"{s.seed} {s.potential}" for s in nb.seeds]
    texts += [c.lately for c in nb.characters]
    texts += [f"{e.state} {e.lately}" for e in world.present_entities()]
    for c in world.clips[-recent:] if recent > 0 else []:
        texts += [c.action, c.end_state, *c.dialogue]
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
            lead = [f"last seen clip {e.last_seen}", where(e)]
            if e.state:
                lead.append(f"then: {_clip(e.state, STATE_CHARS)}")
            out.append(_character(e, portrayed, [b for b in lead if b]))
    rest = [e for e in away if e.id not in full_ids]
    if rest:
        def about(e) -> str:
            bits = [f"last seen clip {e.last_seen}", e.state, where(e)]
            return " (" + ", ".join(b for b in bits if b) + ")"
        out.append(("Other characters" if full else "Characters") + " not in the scene: "
                   + "; ".join(f"`{e.id}`{about(e)}" for e in rest))
    dormant = world.dormant_props()
    if dormant:
        out.append(
            "Dormant props (introduced earlier, not in the scene; fair game for callbacks): "
            + ", ".join(f"`{e.id}` (last seen clip {e.last_seen}"
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
EARLIER_INPUTS_FOR_THEN = 10
RECENT_MARKS = 2


def _in_play(world: WorldModel, recall: list[Entity], text: str = "") -> set[str]:
    """What the moment touches: everything in the scene, the place, the
    absent characters recalled, and whatever `text` names."""
    ids = {e.id for e in world.present_entities()} | {e.id for e in recall} | world.mentions(text)
    if world.camera_location:
        ids.add(world.camera_location)
    return ids


def _at(clip: int) -> str:
    return f"clip {clip}" if clip else "before the first clip"


def _evidence(inputs: dict[int, Input], grounds: list[int], *, ids: bool) -> str:
    """What a reading rests on, as counted from the inputs it cites."""
    cited = [inputs[g] for g in grounds if g in inputs]
    at = sorted(s.clip for s in cited)
    bits = [f"{len(cited)} input{'s' if len(cited) != 1 else ''}"]
    if ids:
        bits[0] += ": " + ", ".join(f"#{s.id}" for s in cited)
    if at:
        bits.append(f"clip {at[0]}" if at[0] == at[-1] else f"clips {at[0]}-{at[-1]}")
    return "; ".join(bits)


def _understanding(viewer: ViewerMemory, *, ids: bool) -> list[str]:
    u = viewer.understanding()
    if u is None:
        return []
    inputs = {s.id: s for s in viewer.inputs()}
    out = [f"- {r.reading} ({_evidence(inputs, r.grounds, ids=ids)})" for r in u.readings]
    if u.lately:
        out.append(f"Lately: {u.lately}")
    if u.unknown:
        out.append(f"Unknown: {u.unknown}")
    return out


def _marks(viewer: ViewerMemory, play: set[str], cap: int, *, ids: bool) -> tuple[list[str], int]:
    """The marks that touch the moment, and the most recent ones, oldest
    first; and how many others are kept but not shown."""
    marks = viewer.marks()
    chosen = {m.id for m in marks if set(m.entities) & play} | {m.id for m in marks[-RECENT_MARKS:]}
    shown = [m for m in marks if m.id in chosen][-cap:]
    out = [f"- {m.mark} ({_at(m.clip)}" + (f"; from {', '.join(f'#{i}' for i in m.inputs)}" if ids else "") + ")"
           for m in shown]
    return out, len(marks) - len(shown)


def _input(viewer: ViewerMemory, said: Input, *, full: bool) -> str:
    """One of the viewer's inputs with what the story did with it; `full`
    adds what was on screen when they typed it and Now's reasons."""
    head = f"- #{said.id} ({_at(said.clip)}"
    if full and said.during:
        head += f", while: {_clip(said.during, 140).rstrip('.')}"
    head += f'): "{said.text}"'
    outcomes = []
    for o in viewer.outcomes():
        if said.id not in o.inputs:
            continue
        if o.outcome in ("fulfilled", "ran its course", "superseded"):
            outcomes.append(f"its direction {o.outcome} at clip {o.clip}")
        else:
            outcomes.append(f"{o.outcome} at clip {o.clip}" + (f" ({o.reason})" if full and o.reason else ""))
    return head + " → " + ("; ".join(outcomes) if outcomes else "not answered on screen yet")


def format_viewer(world: WorldModel, viewer: ViewerMemory, current_input: str, *, answer_clips: int = 0,
                  recall: list[Entity] = ()) -> str:
    """The viewer as Now reads it: this beat's input or their active
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
            inputs = {s.id: s for s in viewer.inputs()}
            during = next((inputs[i].during for i in d.inputs if i in inputs and inputs[i].during), "")
            age = world.clip - d.clip
            out.append(
                f'Their direction: "{d.text}", taken up at clip {d.clip} '
                f"({age} clip{'s' if age != 1 else ''} ago)"
                + (f"; they gave it while: {_clip(during, 140).rstrip('.')}" if during else "")
                + f". You took it as: {d.reason or '(no reason given)'}. "
                "Build on it while it has life; say in `direction` when it is fulfilled or has run its course."
            )
    owed = world.owed()
    if owed:
        out.append("Earlier inputs still owed (deferred):")
        out.extend(f'- #{i.id} (clip {i.clip}): "{i.text}" (deferred: {i.reason or "no reason given"})'
                   for i in owed[-4:])
    handled = [o for o in viewer.outcomes() if o.outcome in ("absorbed", "declined", "delivered", "let go")]
    if handled:
        inputs = {s.id: s for s in viewer.inputs()}
        out.append("Recently handled:")
        out.extend(f'- clip {o.clip}: "{" ".join(inputs[i].text for i in o.inputs if i in inputs)}" → {o.outcome}'
                   for o in handled[-3:])
    understanding = _understanding(viewer, ids=False)
    if understanding:
        out.append("Then's understanding of them (each reading with the inputs it rests on):")
        out.extend(understanding)
    marks, _ = _marks(viewer, _in_play(world, recall, text), MARKS_FOR_NOW, ids=False)
    if marks:
        out.append("Their marks on this story that touch the moment (what came from them):")
        out.extend(marks)
    return "\n".join(out)


def format_viewer_then(world: WorldModel, viewer: ViewerMemory, *, upto: int, recall: list[Entity] = ()) -> str:
    """The viewer as Then reads it: its understanding as last written, the
    inputs new since then (or newly answered) in full, earlier inputs the
    moment or its readings touch, and the marks that touch the moment.
    `upto` is how many records the memory holds for this run; everything
    else stays in the memory, counted."""
    u = viewer.understanding()
    inputs = [r for r in viewer.records[:upto] if isinstance(r, Input)]
    fresh = viewer.records[u.seen if u else 0:upto]
    new_ids = {r.id for r in fresh if isinstance(r, Input)}
    new_ids |= {i for r in fresh if isinstance(r, Outcome) for i in r.inputs}
    new = [s for s in inputs if s.id in new_ids]

    understanding = _understanding(viewer, ids=True)
    out = ["Your understanding of them as last written:" if understanding
           else "(Your understanding of them is empty so far.)"]
    out.extend(understanding)
    if new:
        out.append("Their inputs new since then, with what was on screen and what the story did:")
        out.extend(_input(viewer, s, full=True) for s in new)
    else:
        out.append("They have said nothing new since." if inputs else "They have said nothing yet.")
    play = _in_play(world, recall, " ".join(s.text for s in new)) | {e for s in new for e in s.entities}
    cited = {g for r in u.readings for g in r.grounds} if u else set()
    older = [s for s in inputs if s.id not in new_ids]
    earlier = [s for s in older if s.id in cited or set(s.entities) & play][-EARLIER_INPUTS_FOR_THEN:]
    if earlier:
        out.append("Earlier inputs this moment or your readings touch:")
        out.extend(_input(viewer, s, full=False) for s in earlier)
    marks, unshown = _marks(viewer, play, MARKS_FOR_THEN, ids=True)
    if marks:
        out.append("Their marks (what came from them):")
        out.extend(marks)
    if len(older) > len(earlier) or unshown:
        out.append(f"(Kept but not shown: {len(older) - len(earlier)} earlier inputs, {unshown} marks.)")
    return "\n".join(out)


def format_summaries(world: WorldModel, recent: int = 3, recall: list[Entity] = ()) -> str:
    """The act summary, the opening, the most recent summaries, and the
    one in which each recalled character was last seen."""
    out: list[str] = []
    summaries = world.summaries
    if summaries:
        first = summaries[0]
        out.append(f"Opening (clips {first.first_clip}-{first.last_clip}): {first.text}")
    shown = summaries[:1] + summaries[-recent:]
    for s in summaries:
        last_here = [f"`{e.id}`" for e in recall if s.first_clip <= e.last_seen <= s.last_clip]
        if last_here and not any(s is x for x in shown):
            out.append(f"Clips {s.first_clip}-{s.last_clip} (when {', '.join(last_here)} "
                       f"{'was' if len(last_here) == 1 else 'were'} last seen): {s.text}")
    if world.act_summary:
        out.append(f"Act so far: {world.act_summary}")
    for s in summaries[-recent:]:
        if s is summaries[0]:
            continue
        out.append(f"Clips {s.first_clip}-{s.last_clip}: {s.text}")
    return "\n\n".join(out) if out else "(nothing summarised yet; the recent clips are the whole story)"


def _clip_label(c) -> str:
    beat = f" · beat {c.beat} \"{c.beat_intent}\" clip {c.beat_clip}/{c.beat_clips}" if c.beat else ""
    return f"[clip {c.clip}{beat}]"


def format_recent(world: WorldModel, count: int) -> str:
    clips = world.clips[-count:] if count > 0 else []
    if not clips:
        return "(no clips yet; you are opening the story)"
    lines = []
    for c in clips:
        lines.append(
            f"{_clip_label(c)} {c.action}\n"
            f"  on screen: {', '.join(c.on_screen) or '(none)'} · ends on: {c.end_state or '(unspecified)'}"
            + (" · said: " + " / ".join(c.dialogue) if c.dialogue else "")
        )
    return "\n\n".join(lines)


def format_checkpoints(world: WorldModel) -> str:
    """The checkpoints so far, and the clips a new one can be named at."""
    out = [f'- "{c.name}" at clip {c.at}' for c in world.checkpoints]
    out = ["So far:", *out] if out else ["None so far."]
    first = (world.checkpoints[-1].at if world.checkpoints else 0) + 1
    if first <= world.clip:
        out.append(f"A new one can be named at clips {first}-{world.clip}.")
    else:
        out.append("No clip can be named yet.")
    return "\n".join(out)


def format_clips(world: WorldModel, since: int) -> str:
    clips = world.clips_since(since)
    if not clips:
        return "(no clips since the last summary)"
    lines = []
    for c in clips:
        bits = [f"{_clip_label(c)} {c.action}"]
        if c.input:
            bits.append(f'  viewer: "{c.input}" → {c.taken}')
        if c.changes:
            bits.append("  world: " + "; ".join(c.changes))
        if c.dialogue:
            bits.append("  said: " + " / ".join(c.dialogue))
        lines.append("\n".join(bits))
    return "\n\n".join(lines)
