"""Prompt compiler: turns one clip of a beat into the video model's dialect.

This is the only place that knows how LTX-2.5 likes to be prompted: one
flowing paragraph with the sound, the camera, who is on screen, the
action, dialogue in quotes and the image the clip ends on. Every clip
continues the previous one, so the prompt describes the next few seconds
of motion, not the picture the conditioning frames already carry: the
full look of each character goes only on the opening clip, which opens
on Frame Zero's scene; an entity's description goes on
the clip in which it enters the picture (introduced, or back after an
absence), and an entering entity is never listed as already on screen,
because the conditioning frames do not have it. Later clips carry the
short look of each character and prop, which holds identity over long
chains: a prop named alone gets redrawn within a few clips. There is no
style paragraph: how anything is drawn is in its look, so the picture's
style changes when looks do. A clip that cuts opens on the shot the
picture is in and says "The view cuts to …" before its action, LTX-2.5's
convention for a cut in prose; the clips after it open on the new shot.
A location with a look is named the same way as a character: in full on
the opening clip and on the clip the picture arrives there (after its
cut, or after the action that gets it there), by its short look on the
clips that continue there. When the model changes, this file changes;
the schemas do not.
"""

from __future__ import annotations

from src.models.frame_zero import Voice
from src.story.schemas import BeatPlan
from src.story.world import Entity, WorldModel


def _sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if text[-1] not in ".!?\"'":
        text += "."
    return text[0].upper() + text[1:]


def _tail(text: str) -> str:
    """Like `_sentence` but keeps the original capitalisation (proper names)."""
    text = " ".join(text.split()).strip()
    if text and text[-1] not in ".!?\"'":
        text += "."
    return text


def describe(entity: Entity, *, full: bool) -> str:
    """The entity as the prompt names it: with its full look when `full`
    (the opening clip, or the clip in which it enters the picture),
    otherwise its short look or, without one, just the name."""
    if full:
        look = " ".join(entity.render_look().split()).rstrip(".")
        if look and look.lower() != entity.name.lower():
            return f"{entity.name} ({look})"
        return entity.name
    short = " ".join(entity.look_short.split()).rstrip(".")
    if short:
        return f"{entity.name} ({short})"
    return entity.name


CAMERA_STATIC = ("static", "static camera", "none", "no movement", "no camera movement",
                 "still", "fixed", "locked off", "locked-off")


def camera_clause(motion: str) -> str:
    """The camera as one clause: "the camera remains static", or the
    Now's motion after "the camera" in a form that reads as a
    sentence whether the motion is a verb phrase ("slowly pushes in"), a
    participle ("tracking him") or a noun phrase ("slow push-in toward
    the pair", the form Now writes today)."""
    text = " ".join(motion.split()).strip().rstrip(".").strip()
    low = text.lower()
    for prefix in ("the camera ", "camera "):
        if low.startswith(prefix):
            text, low = text[len(prefix):], low[len(prefix):]
    if not low or low in CAMERA_STATIC or low.startswith("static"):
        return "the camera remains static"
    words = low.split()
    head = next((w for w in words if not w.endswith("ly")), words[0])
    if head.endswith("ing"):
        return f"the camera is {text}"
    if head.endswith("s") and not head.endswith("ss"):
        return f"the camera {text}"
    article = "an" if low[0] in "aeiou" else "a"
    return f"the camera makes {article} {text}"


def _voice_words(text: str) -> str:
    """A voice description as the words between "in a" and "voice":
    "a deep, gravelly voice" → "deep, gravelly"."""
    text = " ".join(text.split()).strip().rstrip(".")
    low = text.lower()
    for article in ("a ", "an "):
        if low.startswith(article):
            text, low = text[len(article):], low[len(article):]
    if low.endswith(" voice"):
        text = text[: -len(" voice")]
    return text.strip(" ,")


def _language(voice: Voice | None) -> str:
    """ "in Norwegian with a soft Bergen accent", or ""."""
    if voice is None:
        return ""
    language, accent = voice.language.strip(), voice.accent.strip().rstrip(".")
    if language and accent:
        return f"in {language} with {accent}"
    if language:
        return f"in {language}"
    if accent:
        return f"with {accent}"
    return ""


# Words that only soften a quality: "slightly formal" says nothing "formal" does not.
_HEDGES = frozenset({"a", "an", "bit", "little", "slightly", "somewhat", "quite", "very", "rather"})


def speech(name: str, line: str, *, delivery: str = "", voice: Voice | None = None) -> str:
    """One spoken line in LTX-2's form: `Name says in a {delivery} voice,
    "line"`, the delivery being the line's own, then the speaker's
    default, then the speaker's vocal characteristics; language and
    accent follow when the voice gives them. A quality the delivery already
    names is not said again ("formal, slightly breathless" then "earnest",
    not "…, slightly formal, a little breathless")."""
    words = [p for p in (x.strip() for x in _voice_words(delivery or (voice.delivery if voice else "")).split(","))
             if p]
    if voice is not None and voice.description.strip():
        said = {w for p in words for w in p.lower().split()}
        for phrase in (x.strip() for x in _voice_words(voice.description).split(",")):
            if phrase and not {w for w in phrase.lower().split() if w not in _HEDGES} <= said:
                words.append(phrase)
    how = ", ".join(words)
    lead = f"{name[:1].upper()}{name[1:]} says" + (f" in {'an' if how[:1].lower() in 'aeiou' else 'a'} {how} voice" if how else "")
    tongue = _language(voice)
    if tongue:
        lead += f", {tongue}"
    text = " ".join(line.split()).replace('"', "'")
    return f'{lead}, "{text}"'


DESCRIBED_IN_FULL = 6   # entities described with a look; the rest by name only


def shot_before(beat: BeatPlan, index: int) -> str:
    """The shot clip `index` of `beat` opens on: the beat's framing, or the
    last cut of an earlier clip in the beat."""
    shot = beat.framing
    for clip in beat.clips[:index]:
        if clip.cut_to.strip():
            shot = clip.cut_to
    return shot


_DETERMINERS = ("a", "an", "the", "his", "her", "its", "their", "our", "one", "another", "this", "that")


def _with_article(shot: str) -> str:
    """ "close-up of the ball" → "a close-up of the ball"; a shot that
    already opens with a determiner is kept."""
    first = shot.split()[0].lower() if shot.split() else ""
    if not first or first in _DETERMINERS:
        return shot
    return ("an " if first[0] in "aeiou" else "a ") + shot


def previous_picture(world: WorldModel, clip_no: int) -> set[str] | None:
    """The entities in the picture at the end of the previous clip: that
    clip's `on_screen` plus whatever entered during it (introduced then),
    so an entity described as entering once is not described as entering
    again on the next clip. Before the story's first clip it is Frame
    Zero's character. None when the picture before is not known (a replay
    with a gap)."""
    if not world.clips:
        first = clip_no <= 1 and world.opening_picture is not None
        return set(world.opening_picture) if first else None
    prev = world.clips[-1]
    if prev.clip != clip_no - 1:
        return None
    picture = set(prev.on_screen)
    picture |= {e.id for e in world.entities.values() if e.first_seen == prev.clip}
    return picture


def previous_location(world: WorldModel, clip_no: int) -> str | None:
    """The location the previous clip ended in, or None when it is not
    known (the opening clip, or a replay with a gap)."""
    if not world.clips:
        return None
    prev = world.clips[-1]
    if prev.clip != clip_no - 1 or not prev.location:
        return None
    return prev.location


def compile_clip(
    beat: BeatPlan,
    index: int,
    world: WorldModel,
    *,
    scene: str = "",
    opening: bool = False,
    clip_no: int = 0,
) -> str:
    """The prompt for clip `index` of `beat`. `opening` marks the story's
    first clip: the renderer seeds it from the reference image, so it
    carries the full looks and opens on `scene`, what that image shows.
    `clip_no` is the clip's number: an entity introduced in it, or on
    screen now but not in the previous clip's picture (read from the
    world's recent clips), enters during this clip and is described then."""
    clip = beat.clips[index]
    parts: list[str] = []
    here = world.current_location()
    place = here if here is not None and here.look.strip() else None
    arriving = (place is not None and not opening
                and previous_location(world, clip_no) not in (None, place.id))
    if place is not None and not arriving:
        parts.append(f"Setting: {describe(place, full=opening)}.")
    if clip.ambient_sound.strip():
        parts.append(_sentence(clip.ambient_sound))
    framing = " ".join(shot_before(beat, index).split()).rstrip(".") or "medium shot"
    cut = " ".join(clip.cut_to.split()).rstrip(".")
    if cut:
        # The clip opens on the shot the picture is in and cuts at once;
        # the camera move belongs to the new shot.
        parts.append(_sentence(framing))
    else:
        parts.append(_sentence(f"{framing}; {camera_clause(clip.camera_motion)}"))

    seen: set[str] = set()
    ents: list[Entity] = []
    for eid in clip.on_screen:
        e = world.resolve(eid)
        if e is not None and e.kind != "location" and e.id not in seen:
            seen.add(e.id)
            ents.append(e)
    # Who enters the picture during this clip: introduced in this clip
    # (listed by Now or not: the world has it present), or on
    # screen now without having been in the previous clip's picture.
    previous = previous_picture(world, clip_no)
    entering: list[Entity] = []
    staying: list[Entity] = []
    for e in ents:
        new = (clip_no > 0 and e.first_seen == clip_no) or (previous is not None and e.id not in previous)
        (entering if new else staying).append(e)
    if clip_no > 0:
        for e in world.entities.values():
            if (e.first_seen == clip_no and e.present and not e.gone
                    and e.kind != "location" and e.id not in seen):
                entering.append(e)
    entering.sort(key=lambda e: e.kind != "character")  # characters first
    staying.sort(key=lambda e: e.kind != "character")
    # The look budget: characters first (identity), then what enters, then
    # the rest; beyond it, names only.
    budget = DESCRIBED_IN_FULL
    described: dict[str, bool] = {}
    staying_ids = {e.id for e in staying}
    for e in sorted(entering + staying, key=lambda e: (e.kind != "character", e.id in staying_ids)):
        described[e.id] = budget > 0
        budget -= 1
    action_low = " ".join(clip.action.split()).lower()

    def entering_name(e: Entity) -> str:
        look = " ".join(e.render_look().split()).rstrip(".")
        if not described[e.id] or (look and look.lower() in action_low):
            return e.name          # the action already carries the look
        return describe(e, full=True)

    named = [describe(e, full=opening) if described[e.id] else e.name for e in staying]
    if named:
        parts.append("On screen: " + "; ".join(named) + ".")
    # The first clip starts from the reference image, so it must open from
    # what that image shows; a pose mismatch in the first frames is what
    # drags the picture off the sheet.
    if opening and scene.strip():
        parts.append(f"The shot opens on this picture: {_sentence(scene)} Then the action begins.")
    if cut:
        parts.append(_sentence(f"The view cuts to {_with_article(cut)}; {camera_clause(clip.camera_motion)}"))
        if arriving:
            parts.append(f"The new setting: {describe(place, full=True)}.")
    if entering:
        parts.append("Entering the picture during this clip: " + "; ".join(entering_name(e) for e in entering) + ".")
    if clip.action.strip():
        parts.append(_sentence(clip.action))
    if arriving and not cut:
        parts.append(f"Arriving in {describe(place, full=True)}.")
    for line in clip.dialogue:
        speaker = world.resolve(line.speaker)
        name = speaker.name if speaker is not None else line.speaker
        parts.append(speech(name, line.line, delivery=line.delivery.strip(),
                            voice=speaker.voice if speaker is not None else None))
    if clip.end_state.strip():
        parts.append("Final frame: " + _tail(clip.end_state))
    return " ".join(p for p in parts if p)
