"""Context assembler: renders the world model into prompt sections.

Everything the Director and Showrunner read comes through here, so the
prompt budget is decided in one place: active entities in full with
each character's current state, dormant ones by name, bounded threads,
recent clips verbatim, older history as summaries. The system prompt
(cast, rules, style) never changes during a run so the API's prompt
cache hits; everything that moves goes into the user prompt.
"""

from __future__ import annotations

from src.models.config import Config
from src.models.story import Narrator
from src.story.world import MAX_NOTES, WorldModel


def format_rules(constraints: list[str]) -> str:
    if not constraints:
        return "(none)"
    return "\n".join(f"- {c}" for c in constraints)


def format_narrator(narrator: Narrator, tone_guidelines: str = "") -> str:
    """The narrator section of the Director's system prompt, from the
    story (AUD-06): whether the film has one, the register of its lines,
    the language to write them in and the story's guidance on when it
    speaks. The film's own tone, when the story gives one apart from the
    narrator's, comes first."""
    out: list[str] = []
    film_tone = " ".join(tone_guidelines.split())
    if film_tone and film_tone != " ".join(narrator.tone.split()):
        out.append(f"Tone of the film: {film_tone}")
    if not narrator.enabled:
        out.append("This film has no narrator. Leave `voiceover` empty.")
        return "\n\n".join(out)
    out.append("Register of the narrator's lines (applies only to the `voiceover` field): "
               + (" ".join(narrator.tone.split()) or "(not specified: plain, observational)"))
    language = narrator.voice.language.strip()
    if language:
        out.append(f"The narrator speaks {language}: write the line in {language}.")
    if narrator.guidance.strip():
        out.append("When the narrator speaks in this story: " + " ".join(narrator.guidance.split()))
    return "\n\n".join(out)


def format_cast(world: WorldModel) -> str:
    """The blueprint's cast sheet (system prompts: it never changes during a
    run, so characters the film introduces are listed in World now)."""
    lines = []
    for ent in world.by_kind("character"):
        if ent.dynamic:
            continue
        bits = [f"- `{ent.id}`: {ent.name}. {ent.description}".rstrip()]
        if ent.role:
            bits.append(f"  Role: {ent.role}.")
        if ent.personality:
            bits.append(f"  Personality: {ent.personality}")
        if ent.invariants:
            bits.append("  Never changes: " + "; ".join(ent.invariants) + ".")
        if ent.appears:
            when = f", when {ent.appears_when.rstrip('.')}" if ent.appears_when else ""
            bits.append(f"  Not in the film at the start; {ent.appears} appear{when}.")
        if ent.look:
            bits.append(f"  Look (verbatim for shots): {ent.look}")
        if ent.reference_pose:
            bits.append(f"  Reference image shows: {ent.reference_pose}. The first clip of the film starts from exactly this; begin the action there.")
        if not ent.speaks:
            bits.append("  Never speaks: no dialogue lines for this character.")
        elif ent.voice is not None:
            voice = ent.voice
            said = "; ".join(x for x in (voice.description, voice.language and f"speaks {voice.language}",
                                          voice.accent, voice.delivery and f"usually {voice.delivery}") if x)
            if said:
                bits.append(f"  Voice: {said}.")
        lines.append("\n".join(bits))
    return "\n".join(lines) if lines else "(none)"


def format_cast_rules(world: WorldModel) -> str:
    """The story's cast and perspective policy (ARCH-19), for both system
    prompts: the protagonist's continuity, whether new characters may be
    introduced and how, and how the film is shot."""
    out: list[str] = []
    hero = world.entities.get(world.protagonist)
    if hero is not None:
        out.append(f"- Protagonist: `{hero.id}` ({hero.name}). Always the same character: never removed from the "
                   "story, never appearing twice, its look and anything under \"Never changes\" held in every clip.")
    supporting = [e for e in world.by_kind("character") if e.appears]
    if supporting:
        out.append("- Supporting characters enter when the story calls for them (see the cast): the Showrunner "
                   "decides when an entrance serves the story, the Director stages it by bringing the character "
                   "in with its id (`update`, `present: true`).")
    policy = world.cast_policy
    if policy.dynamic_characters:
        guide = " ".join(policy.dynamic_guidance.split()) or "Invent them freely within the rules of the world."
        out.append(f"- New characters beyond the cast are allowed. {guide} Introduce one with `introduce`, kind "
                   "`character`, a `description` of how it looks, its `role`, `speaks`, and a `voice` if it speaks; "
                   "it is then registered and must look and sound the same in every later clip.")
    else:
        out.append("- No characters beyond the cast: anything else that appears is a prop.")
    persp = world.perspective
    if persp.allowed:
        rule = f" {' '.join(persp.switching.split())}" if persp.switching.strip() else ""
        out.append(f"- Perspective: the film is shot {persp.default}. A beat may switch to "
                   f"{' or '.join(persp.allowed)}; set `perspective` on that beat, otherwise leave it empty.{rule}")
    else:
        out.append(f"- Perspective: the film is shot {persp.default}, always. Leave `perspective` empty.")
    return "\n".join(out)


def format_locations(world: WorldModel) -> str:
    lines = [f"- `{e.id}`: {e.name}. {e.description}" for e in world.by_kind("location")]
    return "\n".join(lines) if lines else "(none)"


def viewer_direction_active(world: WorldModel, current_input: str = "") -> bool:
    """A viewer's direction is active while this beat answers a line or the
    last absorbed line's momentum has not expired (ARCH-08)."""
    return bool((current_input or "").strip() or world.viewer_momentum)


def format_plan(world: WorldModel, config: Config, *, hide_candidates: bool = False) -> str:
    """The arc and the scene card. The Director's copy hides the card's
    candidate beats while a viewer's direction is active (ARCH-09): the card
    is the menu for the quiet stretches, and a visible menu pulled the film
    back from the viewer's direction in X-07/X-08. The Showrunner always sees
    its own candidates."""
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
            f"running {world.scene_seconds():.0f} s of about {config.scene_seconds:.0f} s):"
        )
        out.append(f"  goal: {card.goal}")
        out.append(f"  tension: {card.tension}")
        out.append(f"  exit condition: {card.exit_condition}")
        out.append(f"  intensity: {card.intensity}")
        if card.candidate_beats and hide_candidates:
            out.append("  candidate beats: hidden while the viewer's direction is active; "
                       "they come back when it expires")
        elif card.candidate_beats:
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
            if ent.kind == "character":
                now = ent.now() or "(not recorded yet: describe it in your first op)"
                held = ", ".join(p.name for p in world.held_props(ent.id))
                line = f"- {ent.id} (character): {ent.name} · now: {_clip(now, STATUS_CHARS)}"
                if ent.dynamic:
                    # Not on the cast sheet (it came into the film at segment
                    # `first_seen`): who it is and whether it speaks.
                    voice = f", voice: {ent.voice.description}" if ent.voice and ent.voice.description else ""
                    line += (f" · introduced at segment {ent.first_seen}"
                             + (f" · role: {ent.role}" if ent.role else "")
                             + (f" · speaks{voice}" if ent.speaks else " · never speaks"))
                if held:
                    line += f" · holding: {held}"
                if ent.location:
                    line += f" · at {ent.location}"
                out.append(line)
                if ent.notes:
                    out.append("  notes: " + " | ".join(ent.notes[-MAX_NOTES:]))
                continue
            status = f" · status: {_clip(ent.status, STATUS_CHARS)}" if ent.status else ""
            where = f" · held by {ent.holder}" if ent.holder else (
                f" · at {ent.location}" if ent.location and ent.kind != "location" else "")
            # No `looks:` here: the renderer's description is the compiler's
            # business (it reads `look` from the model); the Director writes
            # what happens, not what things look like.
            out.append(f"- {ent.id} ({ent.kind}): {ent.name}{status}{where}")
        if rest:
            out.append("Also present (seen less recently): " + ", ".join(f"`{e.id}`" for e in rest))
    else:
        out.append("Present in the scene: (nothing but the location)")
    # Characters not in the scene still have a state worth knowing.
    away = [e for e in world.by_kind("character") if not e.present and not e.gone]
    if away:
        out.append("Characters not in the scene: " + "; ".join(
            f"`{e.id}`" + (" (not yet in the film)" if not e.first_seen and not e.last_seen and e.appears
                           else f" ({e.now()})" if e.now() else "") for e in away))
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
    """Open threads, newest last, each with its age (segments and story
    seconds since it opened) so a stale one is visible; nothing expires
    them in code (ARCH-16)."""
    threads = world.open_threads()
    if not threads:
        return "(none open)"
    shown = threads[-limit:]
    lines = []
    for t in shown:
        ago = max(0, world.segment - t.opened_at)
        age = f"{ago} segment{'s' if ago != 1 else ''}, {world.seconds_since(t.opened_at):.0f} s of film ago"
        lines.append(f"- `{t.id}` [{t.kind}, opened segment {t.opened_at}, {age}] {t.text}")
    if len(threads) > limit:
        lines.insert(0, f"({len(threads) - limit} older open threads not shown)")
    return "\n".join(lines)


def format_interventions(world: WorldModel, current_input: str) -> str:
    out: list[str] = []
    text = (current_input or "").strip()
    if text:
        out.append(
            f'Viewer input for this beat: "{text}"\n'
            "Decide its disposition: absorbed (it happens now), deferred (it needs "
            "setup; say what you will do), or declined (only if it breaks the rules)."
        )
    else:
        out.append("No viewer input for this beat.")
        if world.viewer_momentum:
            out.append(
                f'The viewer\'s last direction (segment {world.viewer_momentum_segment}): '
                f'"{world.viewer_momentum}"\n'
                "This energy should still shape your beats — develop it naturally, "
                "build on its consequences, let the story grow from it. Don't snap "
                "back to the scene card as if the viewer never spoke."
            )
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
    return "\n\n".join(out) if out else "(nothing compacted yet; the recent clips are the whole story)"


def _clip_label(ev) -> str:
    beat = f" · beat {ev.beat} \"{ev.beat_intent}\" clip {ev.clip_index}/{ev.clip_count}" if ev.beat else ""
    return f"[segment {ev.segment}{beat}]"


def format_recent(world: WorldModel, count: int) -> str:
    events = world.events[-count:] if count > 0 else []
    if not events:
        return "(no clips yet; you are opening the film)"
    lines = []
    for ev in events:
        lines.append(
            f"{_clip_label(ev)} {ev.narration}\n"
            f"  on screen: {', '.join(ev.on_screen) or '(none)'} · ends on: {ev.end_state or '(unspecified)'}"
            + (f" · voice-over: \"{ev.voiceover}\"" if ev.voiceover else "")
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
        if ev.thread_ops:
            bits.append("  threads: " + "; ".join(ev.thread_ops))
        if ev.voiceover:
            bits.append(f'  voice-over: "{ev.voiceover}"')
        lines.append("\n".join(bits))
    return "\n\n".join(lines)


def format_gates(
    *, voiceover_open: bool, pacing: str, scene: str, narrator: bool = True, answer_clips: int = 0,
) -> str:
    """The deterministic signals for this beat. `narrator` is whether the
    story has one (AUD-06; the rendering choice `narration` never closes
    the gate). `answer_clips` > 0 when the beat answers a viewer line: its
    clip cap (ARCH-11)."""
    if not narrator:
        vo = "Voice-over: this film has no narrator. Leave `voiceover` empty."
    elif voiceover_open:
        vo = (
            "Voice-over floor: OPEN. You may fill `voiceover` with one short line "
            "if it earns its place; empty is still the normal choice."
        )
    else:
        vo = "Voice-over floor: CLOSED (a line played recently). Leave `voiceover` empty."
    out = f"{vo}\nPacing: {pacing}\n{scene}"
    if answer_clips:
        out += (f"\nThis beat answers the viewer: at most {answer_clips} clip{'s' if answer_clips != 1 else ''}. "
                "Start the answer now; the next beat continues it.")
    return out
