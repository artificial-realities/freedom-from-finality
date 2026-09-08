"""Deterministic pacing: the voice-over gate, the story clock, and the
stagnation ladder that turns "nothing has changed for a while" into
prompt pressure and, eventually, a Showrunner replan.
"""

from __future__ import annotations

from src.models.config import Config
from src.state.story_state import StoryState
from src.story.world import WorldModel


def voiceover_gate(state: StoryState, config: Config) -> bool:
    """True when the Director may fill `voiceover` this segment."""
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
    n = world.stagnation()
    t = max(1, config.stagnation_threshold)
    if n < t:
        return "The story is moving; no pacing pressure."
    if n < 2 * t:
        return (
            f"{n} segments since anything in the world changed. Land a concrete "
            "development this segment: a new prop, a reveal, a reversal, something "
            "changing hands or place, or a thread paying off."
        )
    return (
        f"{n} segments without structural change. A structural shift is REQUIRED "
        "this segment (new prop, new goal, a payoff, a failure that changes the "
        "situation). If the scene has run out, set scene_done or needs_replan."
    )


def scene_note(world: WorldModel, config: Config) -> str:
    card = world.plan.scene
    if card is None:
        return "No scene card yet."
    length = world.scene_length()
    cap = config.max_segments_per_scene
    if length >= cap:
        return (
            f"Scene {card.id} has reached its cap ({cap} segments). Bring it to its "
            "exit condition now and set scene_done."
        )
    if length >= cap - 2:
        return f"Scene {card.id} is at segment {length} of at most {cap}: steer toward the exit condition."
    return f"Scene {card.id}, segment {length} of at most {cap}."


def stagnation_report(world: WorldModel, config: Config) -> str:
    n = world.stagnation()
    parts = [f"{n} segments since the last structural change (threshold {config.stagnation_threshold})."]
    card = world.plan.scene
    if card is not None:
        parts.append(f"Current scene has run {world.scene_length()} segments (cap {config.max_segments_per_scene}).")
    dormant = world.dormant_props()
    if dormant:
        parts.append("Dormant props available for callbacks: " + ", ".join(e.id for e in dormant) + ".")
    return " ".join(parts)
