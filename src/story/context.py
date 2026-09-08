"""Context assembler: renders the world model into prompt sections.

Everything the Director and Showrunner read comes through here, so the
prompt budget is decided in one place: active entities in full, dormant
ones by name, bounded threads, recent beats verbatim, older history as
summaries.
"""

from __future__ import annotations

from src.models.config import Config
from src.story.world import WorldModel


def format_rules(constraints: list[str]) -> str:
    if not constraints:
        return "(none)"
    return "\n".join(f"- {c}" for c in constraints)


def format_cast(world: WorldModel) -> str:
    lines = []
    for ent in world.by_kind("character"):
        bits = [f"- `{ent.id}`: {ent.name}. {ent.description}".rstrip()]
        if ent.look:
            bits.append(f"  Look (verbatim for shots): {ent.look}")
        if not ent.speaks:
            bits.append("  Never speaks: no dialogue lines for this character.")
        for note in ent.notes[-6:]:
            bits.append(f"  Note: {note}")
        lines.append("\n".join(bits))
    return "\n".join(lines) if lines else "(none)"


def format_locations(world: WorldModel) -> str:
    lines = [f"- `{e.id}`: {e.name}. {e.description}" for e in world.by_kind("location")]
    return "\n".join(lines) if lines else "(none)"


def format_plan(world: WorldModel, config: Config) -> str:
    out: list[str] = []
    if world.plan.arc:
        out.append("Arc:")
        for i, act in enumerate(world.plan.arc):
            marker = "▶" if i == world.plan.current_act else " "
            out.append(f"{marker} act {i + 1}: {act}")
    else:
        out.append("Arc: (not yet planned)")
    card = world.plan.scene
    if card is None:
        out.append("Scene card: (none)")
    else:
        out.append(
            f"Scene {card.id} (opened at segment {card.opened_at}, "
            f"segment {world.scene_length()} of at most {config.max_segments_per_scene}):"
        )
        out.append(f"  goal: {card.goal}")
        out.append(f"  tension: {card.tension}")
        out.append(f"  exit condition: {card.exit_condition}")
        out.append(f"  intensity: {card.intensity}")
        if card.candidate_beats:
            out.append("  candidate beats (options, not a script):")
            out.extend(f"    - {b}" for b in card.candidate_beats)
    return "\n".join(out)


PRESENT_IN_FULL = 12      # most recently seen present entities get a full line
STATUS_CHARS = 120        # status text is clipped in the prompt, not in the model
GONE_LISTED = 12


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def format_world_now(world: WorldModel) -> str:
    out: list[str] = []
    here = world.current_location()
    out.append(f"Location: `{here.id}` {here.name}" if here else "Location: (none)")
    present = world.present_entities()
    if present:
        # Characters first, then props by recency; a long-running film can
        # have dozens of props in play and the prompt must stay bounded.
        present.sort(key=lambda e: (e.kind != "character", -e.last_seen))
        full, rest = present[:PRESENT_IN_FULL], present[PRESENT_IN_FULL:]
        out.append("Present in the scene:")
        for ent in full:
            status = f" · status: {_clip(ent.status, STATUS_CHARS)}" if ent.status else ""
            where = f" · held by {ent.holder}" if ent.holder else (
                f" · at {ent.location}" if ent.location and ent.kind != "location" else "")
            look = f" · looks: {_clip(ent.render_look(), STATUS_CHARS)}" if ent.kind == "prop" and ent.render_look() != ent.name else ""
            out.append(f"- {ent.id} ({ent.kind}): {ent.name}{status}{where}{look}")
        if rest:
            out.append("Also present (seen less recently): " + ", ".join(f"`{e.id}`" for e in rest))
    else:
        out.append("Present in the scene: (nothing but the location)")
    dormant = world.dormant_props()
    if dormant:
        out.append(
            "Dormant props (introduced earlier, not in the scene; fair game for callbacks): "
            + ", ".join(f"`{e.id}` (last seen segment {e.last_seen})" for e in dormant)
        )
    gone = world.gone_props()
    if gone:
        shown = gone[-GONE_LISTED:]
        more = f" (+{len(gone) - len(shown)} more)" if len(gone) > len(shown) else ""
        out.append("Gone for good: " + ", ".join(f"`{e.id}`" for e in shown) + more)
    return "\n".join(out)


def format_threads(world: WorldModel, limit: int = 8) -> str:
    threads = world.open_threads()
    if not threads:
        return "(none open)"
    shown = threads[-limit:]
    lines = [f"- `{t.id}` [{t.kind}, opened segment {t.opened_at}] {t.text}" for t in shown]
    if len(threads) > limit:
        lines.insert(0, f"({len(threads) - limit} older open threads not shown)")
    return "\n".join(lines)


def format_interventions(world: WorldModel, current_input: str) -> str:
    out: list[str] = []
    text = (current_input or "").strip()
    if text:
        out.append(
            f'Viewer input this segment: "{text}"\n'
            "Decide its disposition: absorbed (it happens now), deferred (it needs "
            "setup; say what you will do), or declined (only if it breaks the rules)."
        )
    else:
        out.append("No viewer input this segment.")
    deferred = [i for i in world.interventions if i.disposition == "deferred"]
    if deferred:
        out.append("Earlier requests still owed (deferred):")
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
    """Act summary, the opening scene (pinned: the start of a film is what
    viewers refer back to), and the most recent scene summaries."""
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
    return "\n\n".join(out) if out else "(nothing compacted yet; the recent segments are the whole story)"


def format_recent(world: WorldModel, count: int) -> str:
    events = world.events[-count:] if count > 0 else []
    if not events:
        return "(no segments yet; you are opening the film)"
    lines = []
    for ev in events:
        lines.append(
            f"[segment {ev.segment}] {ev.narration}\n"
            f"  on screen: {', '.join(ev.on_screen) or '(none)'} · ends on: {ev.end_state or '(unspecified)'}"
            + (f" · voice-over: \"{ev.voiceover}\"" if ev.voiceover else "")
        )
    last = events[-1]
    lines.append(f"Direction set after the last segment: {last.next_direction or '(none)'}")
    return "\n\n".join(lines)


def format_events(world: WorldModel, since: int) -> str:
    events = world.events_since(since)
    if not events:
        return "(no events since the last compaction)"
    lines = []
    for ev in events:
        bits = [f"[segment {ev.segment}] {ev.narration}"]
        if ev.user_input:
            bits.append(f'  viewer: "{ev.user_input}" → {ev.intervention}')
        if ev.applied_ops:
            bits.append("  world: " + "; ".join(ev.applied_ops))
        if ev.thread_ops:
            bits.append("  threads: " + "; ".join(ev.thread_ops))
        if ev.voiceover:
            bits.append(f'  voice-over: "{ev.voiceover}"')
        lines.append("\n".join(bits))
    return "\n\n".join(lines)


def format_picture(world: WorldModel, threshold: int) -> str:
    """What the renderer actually showed last time, for the Director."""
    obs = world.picture
    if obs is None:
        return "No rendered frame has been checked yet."
    lines = [f"Last rendered segment checked: {obs.segment}. Drift {obs.drift}/3. {obs.summary}".strip()]
    for check in obs.entities:
        ent = world.resolve(check.entity)
        name = ent.id if ent else check.entity
        if not check.visible:
            lines.append(f"- {name}: not visible")
        elif check.matches:
            lines.append(f"- {name}: matches the sheet")
        else:
            lines.append(f"- {name}: DEVIATES: {check.deviations}")
    if not obs.background_ok:
        lines.append(f"- background: {obs.background_note or 'does not match the style'}")
    if obs.drift >= threshold:
        lines.append(
            "The picture has drifted from the sheets. Open the next segment with a `cut` so the "
            "renderer re-seeds from the reference (the system will force one otherwise)."
        )
    return "\n".join(lines)


def format_gates(
    *, voiceover_open: bool, pacing: str, scene: str, narration_mode: str,
) -> str:
    if narration_mode == "off":
        vo = "Voice-over: not part of this film. Leave `voiceover` empty."
    elif voiceover_open:
        vo = (
            "Voice-over floor: OPEN. You may fill `voiceover` with one short line "
            "if it earns its place; empty is still the normal choice."
        )
    else:
        vo = "Voice-over floor: CLOSED (a line played recently). Leave `voiceover` empty."
    return f"{vo}\nPacing: {pacing}\n{scene}"
