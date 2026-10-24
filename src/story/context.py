"""Context assembler: renders the world model into prompt sections.

Everything Now and Then read comes through here, so the prompt budget
is decided in one place: active entities in full with each character's
current state, dormant ones by name, recent clips verbatim, older
history as summaries. The system prompts (each role's craft, the
content policy, the output contract) never change during a run so the
API's prompt cache hits; everything that moves, every character
included, goes into the user prompt.
"""

from __future__ import annotations

from src.story.world import WorldModel
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
            out.append(f"- `{ch.entity}`: {ch.portrait}")
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
STATE_CHARS = 160
GONE_LISTED = 12


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def format_world_now(world: WorldModel) -> str:
    out: list[str] = []
    here = world.current_location()
    if here is None:
        out.append("Location: (none)")
    else:
        drawn = " ".join((here.look_short or here.look).split()).rstrip(".")
        out.append(f"Location: `{here.id}` {here.name}" + (f" · drawn as: {drawn}" if drawn else ""))
    portrayed = {e.id for c in world.notebook.characters if (e := world.resolve(c.entity)) is not None}
    present = world.present_entities()
    if present:
        present.sort(key=lambda e: (e.kind != "character", -e.last_seen))
        full, rest = present[:PRESENT_IN_FULL], present[PRESENT_IN_FULL:]
        out.append("Present in the scene:")
        for ent in full:
            if ent.kind == "character":
                now = ent.state or "(not recorded yet: describe it in your first op)"
                line = f"- {ent.id} (character): {ent.name} · now: {_clip(now, STATE_CHARS)}"
                if ent.look:
                    line += f" · looks: {ent.look}"
                if ent.dynamic:
                    line += f" · introduced at segment {ent.first_seen}"
                # Who a character is starts here and moves to Then's
                # portrait once there is one, so it can change.
                if ent.description and ent.id not in portrayed:
                    line += f" · who: {ent.description}"
                if ent.voice and ent.voice.description:
                    line += f" · voice: {ent.voice.description}"
                out.append(line)
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

    away = [e for e in world.by_kind("character") if not e.present and not e.gone]
    if away:
        def about(e) -> str:
            bits = [e.state, elsewhere(e) and f"at `{elsewhere(e)}`"]
            said = ", ".join(b for b in bits if b)
            return f" ({said})" if said else ""
        out.append("Characters not in the scene: " + "; ".join(f"`{e.id}`{about(e)}" for e in away))
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


def format_summaries(world: WorldModel, scene_limit: int = 3) -> str:
    """Act summary, the opening scene, and the most recent scene summaries."""
    out: list[str] = []
    scenes = [s for s in world.summaries if s.scope == "scene"]
    if scenes:
        first = scenes[0]
        out.append(f"Opening (segments {first.first_segment}-{first.last_segment}): {first.text}")
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
