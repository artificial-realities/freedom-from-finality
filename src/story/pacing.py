"""Deterministic pacing: the voice-over gate, the story clock, and the
stagnation ladder that turns "nothing has changed for a while" into
prompt pressure and, eventually, a Showrunner replan.

Pressure is counted in story seconds, not clips: a film that holds a
quiet state for a minute is not stalled, one that holds it for three
minutes is.
"""

from __future__ import annotations

from src.models.config import Config
from src.state.story_state import StoryState
from src.story.world import WorldModel

# The stagnation ladder, in multiples of `stagnation_seconds`: pressure on
# the Director from 1x, a REQUIRED shift from 1.5x, and a Showrunner replan
# from 2x, one beat after the Director was required to act, so each rung
# has one owner (ARCH-05).
REQUIRED_AT = 1.5
SHOWRUNNER_REPLAN_AT = 2.0


def voiceover_gate(state: StoryState, config: Config) -> bool:
    """True when the Director may fill `voiceover` this beat."""
    if config.narration == "off":
        return False
    if state.audio_seconds_owed > 0.001:
        return False
    if not any(h.commentary.voiceover for h in state.history):
        return True  # no line yet: an opening line is welcome
    return state.silence_seconds >= config.min_pause_seconds


def tick_story_clock(state: StoryState, config: Config) -> None:
    """Silence bookkeeping for modes without a live audio producer.

    One segment ≈ one clip of its own duration. A spoken line resets the
    floor; silence accumulates.
    """
    if state.current_commentary and state.current_commentary.voiceover:
        state.silence_seconds = 0.0
    else:
        clip = state.last_clip_duration
        if not clip and state.current_shot is not None:
            clip = float(state.current_shot.duration_seconds)
        state.silence_seconds += clip or float(config.i2v_duration)


def pacing_note(world: WorldModel, config: Config) -> str:
    quiet = world.seconds_since_change()
    t = max(1.0, config.stagnation_seconds)
    if quiet < t:
        return "The story is moving; no pacing pressure. A quiet beat is fine."
    if quiet < REQUIRED_AT * t:
        return (
            f"{quiet:.0f} s of film since anything in the world changed. Land a concrete "
            "development in this beat or the next: introduce a new prop, reveal "
            "something, change who holds what, pay off a thread — anything that "
            "moves the world, not just the character's posture."
        )
    return (
        f"{quiet:.0f} s of film without structural change. A structural shift is REQUIRED "
        "NOW: introduce a prop (just have it appear), pay off or escalate, change "
        "the situation. The audience is watching a character do the same thing. "
        "If the scene has run out, set scene_done or needs_replan."
    )


def scene_note(world: WorldModel, config: Config) -> str:
    card = world.plan.scene
    if card is None:
        return "No scene card yet."
    ran = world.scene_seconds()
    cap = max(1.0, config.scene_seconds)
    if ran >= cap:
        return (
            f"Scene {card.id} has run {ran:.0f} s, past its intended length ({cap:.0f} s). Bring it to its "
            "exit condition in this beat and set scene_done."
        )
    if ran >= 0.75 * cap:
        return f"Scene {card.id} has run {ran:.0f} s of about {cap:.0f} s: steer toward the exit condition."
    return f"Scene {card.id} has run {ran:.0f} s of about {cap:.0f} s; there is time."


def stagnation_report(world: WorldModel, config: Config) -> str:
    quiet = world.seconds_since_change()
    t = config.stagnation_seconds
    parts = [f"{quiet:.0f} s of film since the last structural change (pressure on the Director from {t:.0f} s, "
             f"a required shift from {REQUIRED_AT * t:.0f} s, your replan from {SHOWRUNNER_REPLAN_AT * t:.0f} s)."]
    card = world.plan.scene
    if card is not None:
        parts.append(f"The current scene has run {world.scene_seconds():.0f} s (intended about {config.scene_seconds:.0f} s).")
    dormant = world.dormant_props()
    if dormant:
        parts.append("Dormant props available for callbacks: " + ", ".join(e.id for e in dormant) + ".")
    return " ".join(parts)
