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

import re

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
    if nb.audience:
        out.append(f"\n**The audience**\n{nb.audience}")
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


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def recalled(world: WorldModel, viewer_input: str = "", recent: int = 6) -> list[Entity]:
    """Absent characters the story is pointing at: left where the picture
    is now, or named, or their place named, in what the roles read this
    call (the viewer's words, Then's notebook, the characters in the
    scene, the recent clips). Those are shown in full, with the summary of
    when they were last seen, so a return is written knowing who comes
    back; the rest stay one line each. Code only matches names: which
    mentions matter is the roles' judgment, and Then keeps one in reach by
    naming it."""
    nb = world.notebook
    texts = [viewer_input, world.viewer_momentum, nb.alive, nb.intentions, nb.audience]
    texts += [i.text for i in world.interventions if i.disposition == "deferred"]
    texts += [f"{s.seed} {s.potential}" for s in nb.seeds]
    texts += [c.lately for c in nb.characters]
    texts += [f"{e.state} {e.lately}" for e in world.present_entities()]
    for ev in world.events[-recent:] if recent > 0 else []:
        texts += [ev.narration, ev.end_state, *ev.dialogue]
    said = _words(" ".join(t for t in texts if t))

    def named(ent: Entity | None) -> bool:
        return ent is not None and any(_words(n) in said for n in (ent.name, ent.id))

    here = world.camera_location
    found = [e for e in world.by_kind("character")
             if not e.present and not e.gone
             and ((here and e.location == here) or named(e) or named(world.entities.get(e.location)))]
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


def format_interventions(world: WorldModel, current_input: str, answer_clips: int = 0) -> str:
    out: list[str] = []
    text = (current_input or "").strip()
    if text:
        out.append(f'Viewer input for this beat: "{text}"')
        if answer_clips:
            out.append(f"This beat answers the viewer: at most {answer_clips} clip{'s' if answer_clips != 1 else ''}. "
                       "Start the answer now; the next beat continues it.")
    else:
        out.append("No viewer input for this beat.")
        if world.viewer_momentum:
            out.append(
                f'The viewer\'s direction is still active (said at segment {world.viewer_momentum_segment}): '
                f'"{world.viewer_momentum}". Keep building on it.'
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
    return "\n".join(out)


def format_interventions_all(world: WorldModel) -> str:
    if not world.interventions:
        return "(no viewer input so far)"
    lines = []
    for i in world.interventions[-12:]:
        suffix = f" ({i.reason})" if i.reason else ""
        lines.append(f'- segment {i.segment}: "{i.text}" → {i.disposition}{suffix}')
    return "\n".join(lines)


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
