"""Attenborough — the voice-over commentator.

Always emits structured text. Pacing is Attenborough's to decide: on
any turn he can either speak or stay silent, and when he speaks he
picks `span_clips` (1–4) to mark how many clips the line should play
over. While spanning, the state's `commentary_hold_remaining` counter
holds the next N-1 turns silent — this module never calls the LLM on
those held turns, it just returns an empty `Commentary` and decrements
the counter.

If `audio_enabled: true`, the runtime pipes `voiceover` through
ElevenLabs TTS — a side effect that never blocks the turn.
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

    # Hold from a previous span is still counting down — stay silent,
    # decrement, and don't burn an LLM call.
    if state.commentary_hold_remaining > 0:
        new_hold = state.commentary_hold_remaining - 1
        interaction_logger.log_event(
            "attenborough_hold",
            state.turn_number,
            {"hold_remaining_before": state.commentary_hold_remaining,
             "hold_remaining_after": new_hold},
        )
        return {
            "current_commentary": Commentary(voiceover="", span_clips=1),
            "commentary_hold_remaining": new_hold,
            "current_audio_path": "",
        }

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
        short_term_narrative=state.short_term_narrative or "(not yet set)",
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
    audio_path = ""
    if config.audio_enabled and tts is not None and commentary.voiceover:
        result_path = await tts.synthesize(commentary.voiceover, turn=state.turn_number)
        interaction_logger.log_tts(
            turn=state.turn_number,
            voice_id=tts.voice_id,
            text=commentary.voiceover,
            audio_path=result_path,
            success=result_path is not None,
        )
        audio_path = result_path or ""

    return {
        "current_commentary": commentary,
        "commentary_hold_remaining": max(0, commentary.span_clips - 1),
        "current_audio_path": audio_path,
    }
