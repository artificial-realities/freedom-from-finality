"""Context assembler: renders the world model into prompt sections.

Everything Spark and Echo read comes through here, so the prompt budget
is decided in one place: active entities in full with each character's
current state, dormant ones by name, recent clips verbatim, older
history as summaries. The system prompt (cast, rules, style) never
changes during a run so the API's prompt cache hits; everything that
moves goes into the user prompt.
"""

from __future__ import annotations

from src.models.story import FrameZero, Picture
from src.story.world import WorldModel


# ── System-prompt sections (cached; stable across a run) ───────────

def format_frame_zero(fz: FrameZero) -> str:
    """The world's identity and truths, for Spark and Echo system prompts."""
    lines = [f"This is **{fz.name}**."]
    if fz.truths:
        lines.append("\nWhat is always true:")
        lines.extend(f"- {t}" for t in fz.truths)
    return "\n".join(lines)


def format_picture(picture: Picture) -> str:
    """Visual style and reference image, for Spark's system prompt."""
    out = [" ".join(picture.style.split()) or "(no style given)"]
    shows = " ".join(picture.reference.shows.split()).rstrip(".") if picture.reference else ""
    if shows:
        out.append(f"The story's first clip starts from a reference image that shows {shows}. Open the action "
                   "from exactly that picture, then move; a character it does not show enters the picture.")
    elif picture.reference:
        out.append("The story's first clip starts from a reference image; open the action from it, then move.")
    else:
        out.append("There is no reference image: the story's first clip is drawn from your words alone.")
    return "\n\n".join(out)


def format_opening(fz: FrameZero) -> str:
    if not fz.opening:
        return ""
    return fz.opening


def format_cast(world: WorldModel) -> str:
    """The frame zero's cast sheet (system prompts: it never changes during
    a run, so characters introduced during the story are listed in World now)."""
    lines = []
    for ent in world.by_kind("character"):
        if ent.dynamic:
            continue
        bits = [f"- `{ent.id}`: {ent.name}. {ent.description}".rstrip()]
        if ent.wants:
            bits.append(f"  Wants: {ent.wants}")
        if ent.nature:
            bits.append(f"  Nature: {ent.nature}")
        if not ent.present:
            bits.append("  Not in the story at the start.")
        if ent.cast_look or ent.look:
            bits.append(f"  Look (verbatim for shots): {ent.cast_look or ent.look}")
        if ent.voice is not None:
            voice = ent.voice
            said = "; ".join(x for x in (voice.description, voice.language and f"speaks {voice.language}",
                                          voice.accent, voice.delivery and f"usually {voice.delivery}") if x)
            if said:
                bits.append(f"  Voice: {said}.")
        lines.append("\n".join(bits))
    return "\n".join(lines) if lines else "(none)"


def format_newcomers(world: WorldModel) -> str:
    if world.newcomers:
        return (f"New characters beyond the starting cast may appear: {world.newcomers} "
                "Introduce one with `introduce`, kind `character`, a `description` of how it looks, "
                "`who` it is in the story and what it wants, and its `voice`.")
    return "No characters beyond the starting cast."


def format_cut_rule(world: WorldModel) -> str:
    if not world.cuts:
        return ("The picture never cuts: a change of angle or distance is a camera move within a clip, "
                "and `cut_to` stays empty.")
    guide = f" {' '.join(world.cutting.split())}" if world.cutting.strip() else ""
    return ("This story allows cuts, and a cut costs the picture: the video model lands it seconds into the clip "
            "and can repaint faces. So cut only when a camera move cannot get there: to another location, or for a "
            "reveal the story calls for; any other change of view is a camera move. Write the new shot in `cut_to` as "
            "size, angle and subject (\"wide shot of the tower on its rock\"), never the current framing again. The "
            "clip cuts at its start: its `action`, `end_state` and `camera_motion` belong to the new shot, which holds "
            "for the rest of the beat and after it. At most one cut per clip, never on the story's first clip. Within a "
            "location a cut changes only the view: everyone and everything keeps its place, posture and holdings, and "
            "the new shot continues the same moment. The video model replaces the place reliably only on a cut: a "
            "walk keeps drawing the place it comes from. So arrive at another location by cutting to it, which jumps "
            "to a later moment there, and give the moving character the new `location` in that clip." + guide)


def format_locations(world: WorldModel) -> str:
    lines = [f"- `{e.id}`: {e.name}. {e.description}" for e in world.by_kind("location")]
    return "\n".join(lines) if lines else "(none)"


# ── User-prompt sections (change every call) ───────────────────────

def format_notebook(world: WorldModel) -> str:
    """Echo's notebook as Spark reads it."""
    nb = world.notebook
    if not nb.characters and not nb.alive and not nb.seeds:
        return "(Echo has not written the notebook yet.)"
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
    present = world.present_entities()
    if present:
        present.sort(key=lambda e: (e.kind != "character", -e.last_seen))
        full, rest = present[:PRESENT_IN_FULL], present[PRESENT_IN_FULL:]
        out.append("Present in the scene:")
        for ent in full:
            if ent.kind == "character":
                now = ent.state or "(not recorded yet: describe it in your first op)"
                line = f"- {ent.id} (character): {ent.name} · now: {_clip(now, STATE_CHARS)}"
                if ent.look_since:
                    line += f" · looks now: {ent.look}"
                if ent.dynamic:
                    line += (f" · introduced at segment {ent.first_seen}"
                             + (f" · who: {ent.description}" if ent.description else "")
                             + (f" · voice: {ent.voice.description}" if ent.voice and ent.voice.description else ""))
                out.append(line)
                continue
            now = f" · now: {_clip(ent.state, STATE_CHARS)}" if ent.state else ""
            looks = f" · looks now: {ent.look}" if ent.look_since else ""
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
