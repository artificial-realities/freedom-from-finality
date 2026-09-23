"""Prompt compiler: turns one clip of a beat into the video model's dialect.

This is the only place that knows how LTX-2.5 likes to be prompted: one
flowing paragraph with the sound, the camera, who is on screen, the
action, dialogue in quotes and the image the clip ends on. Every clip
continues the previous one, so the prompt describes the next few seconds
of motion, not the picture the conditioning frames already carry: the
full visual style and the full look of a character go only on the
opening clip of the run; an entity's description goes on the clip in
which it enters the picture (introduced, or back after an absence), and
an entering entity is never listed as already on screen, because the
conditioning frames do not have it. Later clips carry the blueprint's
short visual anchor and each character's short look, which hold framing
and identity over long chains. When the model changes, this file
changes; the schemas do not.
"""

from __future__ import annotations

from src.models.story import Narrator, Voice, perspective_key
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


def _first_sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    for stop in (". ", "; "):
        cut = text.find(stop)
        if cut > 0:
            return text[: cut + 1].strip()
    return text


def describe(entity: Entity, *, full: bool) -> str:
    """The entity as the prompt names it: with its full look when `full`
    (the opening clip, or the clip in which it enters the picture),
    otherwise a character's short look or just the name."""
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
    Director's motion after "the camera" in a form that reads as a
    sentence whether the motion is a verb phrase ("slowly pushes in"), a
    participle ("tracking him") or a noun phrase ("slow push-in toward
    the pair", the form the Director writes today)."""
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


def speech(name: str, line: str, *, delivery: str = "", voice: Voice | None = None) -> str:
    """One spoken line in LTX-2's form: `Name says in a {delivery} voice,
    "line"`, the delivery being the line's own, then the speaker's
    default, then the speaker's vocal characteristics; language and
    accent follow when the voice gives them (LTX-05)."""
    words = [_voice_words(delivery or (voice.delivery if voice else ""))]
    if voice is not None and voice.description.strip():
        words.append(_voice_words(voice.description))
    how = ", ".join(w for w in words if w)
    lead = f"{name} says" + (f" in a {how} voice" if how else "")
    tongue = _language(voice)
    if tongue:
        lead += f", {tongue}"
    text = " ".join(line.split()).replace('"', "'")
    return f'{lead}, "{text}"'


def narration(line: str, narrator: Narrator) -> str:
    """The narrator's line, spoken over the beat's first clip, described
    from the story's narrator: its language, voice and accent, as the
    blueprint gives them; nothing is assumed."""
    voice = narrator.voice
    bits = [voice.language.strip(), _voice_words(voice.description), voice.accent.strip().rstrip(".")]
    hint = ", ".join(b for b in bits if b)
    text = " ".join(line.split()).replace('"', "'")
    return f'A narrator\'s voice-over{f" ({hint})" if hint else ""} says: "{text}"'


DESCRIBED_IN_FULL = 6   # entities described with a look; the rest by name only


def previous_picture(world: WorldModel, segment_no: int) -> set[str] | None:
    """The entities in the picture at the end of the previous clip: that
    clip's `on_screen` plus whatever entered during it (introduced then),
    so an entity described as entering once is not described as entering
    again on the next clip. None when the previous clip is not on record
    (the first clip of a run, or a replay with a gap)."""
    if not world.events:
        return None
    prev = world.events[-1]
    if prev.segment != segment_no - 1:
        return None
    picture = set(prev.on_screen)
    picture |= {e.id for e in world.entities.values() if e.first_seen == prev.segment}
    return picture


def compile_clip(
    beat: BeatPlan,
    index: int,
    world: WorldModel,
    *,
    visual_style: str,
    visual_anchor: str = "",
    narration_mode: str,
    narrator: Narrator | None = None,
    opening: bool = False,
    segment_no: int = 0,
) -> str:
    """The prompt for clip `index` of `beat`. `opening` marks the first
    clip of a run: the renderer seeds it from the reference image, so it
    carries the full style, the full looks and the reference pose.
    `segment_no` is the clip's segment: an entity introduced in it, or
    on screen now but not in the previous clip's picture (read from the
    world's event log), enters during this clip and is described then."""
    clip = beat.clips[index]
    parts: list[str] = []
    if clip.ambient_sound.strip():
        parts.append(_sentence(clip.ambient_sound))
    framing = " ".join(beat.framing.split()).rstrip(".") or "medium shot"
    if (perspective_key(beat.perspective) or perspective_key(world.perspective.default)) == "first-person":
        framing = f"first-person point of view, {framing}"
    parts.append(_sentence(f"{framing}; {camera_clause(clip.camera_motion)}"))

    seen: set[str] = set()
    ents: list[Entity] = []
    for eid in clip.on_screen:
        e = world.resolve(eid)
        if e is not None and e.kind != "location" and e.id not in seen:
            seen.add(e.id)
            ents.append(e)
    # Who enters the picture during this clip: introduced in this segment
    # (listed by the Director or not: the world has it present), or on
    # screen now without having been in the previous clip's picture.
    previous = previous_picture(world, segment_no)
    entering: list[Entity] = []
    staying: list[Entity] = []
    for e in ents:
        new = (segment_no > 0 and e.first_seen == segment_no) or (previous is not None and e.id not in previous)
        (entering if new else staying).append(e)
    if segment_no > 0:
        for e in world.entities.values():
            if (e.first_seen == segment_no and e.present and not e.gone
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
            return e.name          # the Director's action already carries the look
        return describe(e, full=True)

    named = [describe(e, full=opening) if described[e.id] else e.name for e in staying]
    if named:
        parts.append("On screen: " + "; ".join(named) + ".")
    # The first clip starts from the reference image, so it must open from
    # what that image shows; a pose mismatch in the first frames is what
    # drags the picture off the sheet (2026-09-08 sweep).
    if opening:
        for ent in staying:
            if ent.reference_pose:
                parts.append(f"The shot opens with {ent.name} {ent.reference_pose.strip().rstrip('.')}, then the action begins.")
                break
    if entering:
        parts.append("Entering the picture during this clip: " + "; ".join(entering_name(e) for e in entering) + ".")
    if clip.action.strip():
        parts.append(_sentence(clip.action))
    for line in clip.dialogue:
        speaker = world.resolve(line.speaker)
        name = speaker.name if speaker is not None else line.speaker
        parts.append(speech(name, line.line, delivery=line.delivery.strip(),
                            voice=speaker.voice if speaker is not None else None))
    if clip.end_state.strip():
        parts.append("Final frame: " + _tail(clip.end_state))
    paragraphs = [" ".join(p for p in parts if p)]

    if index == 0 and beat.voiceover.strip() and narration_mode == "ltx":
        paragraphs.append(narration(beat.voiceover, narrator or Narrator()))

    if opening:
        style = " ".join(visual_style.split())
    else:
        style = " ".join(visual_anchor.split()) or _first_sentence(visual_style)
    return ((style + " " if style else "") + " ".join(paragraphs)).strip()
