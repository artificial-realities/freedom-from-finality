"""Attenborough — the voice-over commentator.

Always produces structured text. If `audio_enabled: true`, the runtime
pipes `voiceover` through ElevenLabs TTS — but TTS is a side effect
and never blocks the turn.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import (
    call_llm_structured,
    format_recent_commentary,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Commentary
from src.state.story_state import StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.util.interaction_logger import InteractionLogger
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("attenborough.system.md")
USER_PATH: Path = prompt_path("attenborough.user.md")


async def run(
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    interaction_logger: InteractionLogger,
    tts: ElevenLabsTTS | None = None,
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
    user_prompt = load_prompt(
        USER_PATH,
        turn_number=state.turn_number,
        beat_narration=state.current_beat.narration,
        beat_action=state.current_beat.action,
        beat_outcome=state.current_beat.outcome,
        shot_camera=state.current_shot.camera,
        shot_motion=state.current_shot.motion,
        shot_end_frame_description=state.current_shot.end_frame_description,
        narrative_memory=state.narrative_memory or "(no memory yet)",
        recent_commentary=format_recent_commentary(state, count=config.context_window_history),
    )

    parsed = await call_llm_structured(
        agent="attenborough",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        logger_obj=interaction_logger,
        turn=state.turn_number,
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
    if config.audio_enabled and tts is not None and commentary.voiceover:
        audio_path = await tts.synthesize(commentary.voiceover, turn=state.turn_number)
        interaction_logger.log_tts(
            turn=state.turn_number,
            voice_id=tts.voice_id,
            text=commentary.voiceover,
            audio_path=audio_path,
            success=audio_path is not None,
        )

    return {"current_commentary": commentary}
