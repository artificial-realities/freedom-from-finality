"""Deterministic pacing: the stagnation ladder that turns "nothing has
changed for a while" into prompt pressure and, eventually, a Showrunner
replan, and the scene clock that tells the Director when to close a scene.

Pressure is counted in story seconds, not clips: a film that holds a
quiet state for a minute is not stalled, one that holds it for three
minutes is.
"""

from __future__ import annotations

from src.models.config import Config
from src.story.world import WorldModel

REPETITION_WINDOW = 4
REPETITION_THRESHOLD = 0.4


def _word_trigrams(text: str) -> set[tuple[str, ...]]:
    words = text.lower().split()
    if len(words) < 3:
        return set()
    return {(words[i], words[i + 1], words[i + 2]) for i in range(len(words) - 2)}


def _repetition_score(world: WorldModel) -> float | None:
    """Average 3-gram Jaccard among the last REPETITION_WINDOW clips'
    actions.  Returns the score when it exceeds the threshold, else None."""
    recent = world.events[-REPETITION_WINDOW:]
    if len(recent) < 3:
        return None
    grams = [_word_trigrams(e.narration) for e in recent]
    total, count = 0.0, 0
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            a, b = grams[i], grams[j]
            if a and b:
                total += len(a & b) / len(a | b)
            count += 1
    avg = total / count if count else 0.0
    return avg if avg >= REPETITION_THRESHOLD else None

# The stagnation ladder, in multiples of `stagnation_seconds`: pressure on
# the Director from 1x, a REQUIRED shift from 1.5x, and a Showrunner replan
# from 2x, one beat after the Director was required to act, so each rung
# has one owner (ARCH-05).
REQUIRED_AT = 1.5
SHOWRUNNER_REPLAN_AT = 2.0


def pacing_note(world: WorldModel, config: Config) -> str:
    quiet = world.seconds_since_change()
    t = max(1.0, config.stagnation_seconds)
    rep = _repetition_score(world)
    looping = rep is not None
    if quiet >= REQUIRED_AT * t:
        suffix = " The last clips repeat nearly the same action." if looping else ""
        return (
            f"{quiet:.0f} s of film without structural change. A structural shift is REQUIRED "
            "NOW: introduce a prop (just have it appear), pay off or escalate, change "
            "the situation. The audience is watching a character do the same thing. "
            f"If the scene has run out, set scene_done or needs_replan.{suffix}"
        )
    if quiet >= t:
        suffix = " The last clips repeat nearly the same action." if looping else ""
        return (
            f"{quiet:.0f} s of film since anything in the world changed.{suffix} Land a concrete "
            "development in this beat or the next: introduce a new prop, reveal "
            "something, change who holds what, pay off a thread — anything that "
            "moves the world, not just the character's posture."
        )
    if looping:
        return (
            f"The last clips repeat nearly the same action (similarity {rep:.0%}). "
            "Break the loop: introduce a new prop, reveal something, change who "
            "holds what, pay off a thread — anything that changes the situation, "
            "not just the character's posture or phrasing."
        )
    return "The story is moving; no pacing pressure. A quiet beat is fine."


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
    rep = _repetition_score(world)
    if rep is not None:
        parts.append(f"The last clips' actions are {rep:.0%} similar (looping).")
    dormant = world.dormant_props()
    if dormant:
        parts.append("Dormant props available for callbacks: " + ", ".join(e.id for e in dormant) + ".")
    return " ".join(parts)
