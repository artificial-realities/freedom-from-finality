"""Attenborough — the voice-over commentator (the Narrator role).

Runs on the narrator clock, not the chunk clock: the turn engine only
invokes him when the silence floor is open (no audio still playing,
`silence_seconds` at or above `min_pause_seconds`). His choice is what
it always was — silence, a short line, or a longer continuous thought —
but mechanical pacing holds are the engine's job now, not his.

He reads the beats accumulated since his last line, so a line can pay
off a thread that built across several silent clips.

If `audio_enabled: true`, the runtime pipes `voiceover` through
ElevenLabs TTS — a side effect that never blocks the turn. The live
producer measures the audio and lets it span as many clips as its
duration warrants.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import (
    call_llm_structured,
    format_beats_since_voiceover,
    format_memory_block,
    format_recent_commentary,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Commentary
from src.state.story_state import StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.util.langfuse_setup import event as langfuse_event
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("attenborough.system.md")
USER_PATH: Path = prompt_path("attenborough.user.md")


def _silence_label(state: StoryState) -> str:
    """Render `silence_seconds` as a hint for the prompt."""
    if not any(h.commentary.voiceover for h in state.history):
        return "(no voiceover yet — an opening line is welcome)"
    return f"~{state.silence_seconds:.0f}s of silence since your last line"


async def run(
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    tts: ElevenLabsTTS | None = None,
    memory=None,
) -> dict:
    if state.current_beat is None or state.current_shot is None:
        logger.warning(
            "Attenborough skipped turn %s — missing beat or shot",
            state.turn_number,
        )
        return {}

    system_prompt = load_prompt(
        SYSTEM_PATH,
        tone_guidelines=state.tone_guidelines,
    )
    # Mem0 mode: retrieve memories relevant to this beat so genuine
    # callbacks can be spotted without the rolling prose blob.
    retrieved: str | None = None
    if memory is not None:
        retrieved = await memory.search_block(state.current_beat.narration)

    user_prompt = load_prompt(
        USER_PATH,
        turn_number=state.turn_number,
        beats_since_voiceover=format_beats_since_voiceover(state),
        beat_narration=state.current_beat.narration,
        beat_action=state.current_beat.action,
        beat_outcome=state.current_beat.outcome,
        short_term_narrative=state.short_term_narrative or "(not yet set)",
        shot_camera=state.current_shot.camera,
        shot_motion=state.current_shot.motion,
        shot_end_frame_description=state.current_shot.end_frame_description,
        # The i2v backend's configured duration is the real clip length —
        # Spielberg's per-shot pick is ignored for fixed-length models.
        clip_duration_seconds=config.i2v_duration,
        memory_block=format_memory_block(state, retrieved),
        recent_commentary=format_recent_commentary(state, count=config.context_window_history),
        silence_label=_silence_label(state),
    )

    parsed = await call_llm_structured(
        agent="attenborough",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        turn=state.turn_number,
        schema=Commentary,
    )

    if parsed is None:
        return {}

    try:
        commentary = Commentary(**parsed)
    except ValidationError as exc:
        logger.warning(
            "Attenborough commentary validation failed on turn %s: %s",
            state.turn_number, exc,
        )
        return {}

    # Opt-in TTS side effect — never blocks on failure.
    audio_path = ""
    if config.audio_enabled and tts is not None and commentary.voiceover:
        result_path = await tts.synthesize(commentary.voiceover, turn=state.turn_number)
        langfuse_event(
            "tts",
            input=commentary.voiceover,
            output=result_path,
            metadata={"turn": state.turn_number, "voice_id": tts.voice_id,
                      "success": result_path is not None},
            level="DEFAULT" if result_path else "WARNING",
        )
        audio_path = result_path or ""

    return {
        "current_commentary": commentary,
        "current_audio_path": audio_path,
    }
