"""Attenborough — the voice-over commentator.

Always emits structured text. He decides whether to speak; the live
producer measures his audio post-TTS and concatenates as many silent
clips behind it as the duration warrants, holding him silent on those
turns by leaving `audio_seconds_owed > 0`. The producer also enforces
a minimum cinematic pause between lines via `silence_seconds`.

Both gates are only applied when the live producer is actively
managing pacing (`pacing_managed=True` and `audio_enabled=True`).
Benchmark and interactive loops always reach the LLM so commentary
text is produced for every turn.

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


def _silent_label(state: StoryState, config: Config) -> str:
    """Render `silence_seconds` as a hint for the prompt."""
    if not (config.audio_enabled and state.pacing_managed):
        return "(silence not tracked in this mode)"
    if not any(h.commentary.voiceover for h in state.history):
        return "(no voiceover yet — opening line is welcome)"
    return f"~{state.silence_seconds:.0f}s of silence since the last voiceover"


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

    # Pacing gates — only enforced when the live producer is bookkeeping.
    if config.audio_enabled and state.pacing_managed:
        if state.audio_seconds_owed > 0.001:
            interaction_logger.log_event(
                "attenborough_hold",
                state.turn_number,
                {"reason": "audio_owed",
                 "audio_seconds_owed": state.audio_seconds_owed},
            )
            return {
                "current_commentary": Commentary(voiceover=""),
                "current_audio_path": "",
            }
        if state.silence_seconds < config.min_pause_seconds:
            interaction_logger.log_event(
                "attenborough_hold",
                state.turn_number,
                {"reason": "min_pause",
                 "silence_seconds": state.silence_seconds,
                 "min_pause_seconds": config.min_pause_seconds},
            )
            return {
                "current_commentary": Commentary(voiceover=""),
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
        clip_duration_seconds=state.current_shot.duration_seconds,
        narrative_memory=state.narrative_memory or "(no memory yet)",
        recent_commentary=format_recent_commentary(state, count=config.context_window_history),
        silence_label=_silent_label(state, config),
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
        "current_audio_path": audio_path,
    }
