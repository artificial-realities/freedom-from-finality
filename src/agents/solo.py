"""Solo — the monolithic baseline.

One LLM call emits `Beat` + `Shot` + `Commentary` + `MemoryUpdate` in
a single structured response. Receives the entire blueprint plus the
full rolling state.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from src.agents._common import (
    call_llm_structured,
    format_characters_full,
    format_locations,
    format_recent_history,
    format_world_state,
    format_world_constraints,
    previous_end_frame,
)
from src.agents.spock import _apply_delta
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Commentary, SoloResponse
from src.state.story_state import StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.util.interaction_logger import InteractionLogger
from src.util.prompt_loader import load_prompt, prompt_path

logger = logging.getLogger(__name__)

SYSTEM_PATH: Path = prompt_path("solo.system.md")
USER_PATH: Path = prompt_path("solo.user.md")


async def run(
    state: StoryState,
    llm: LLMBackend,
    config: Config,
    interaction_logger: InteractionLogger,
    tts: ElevenLabsTTS | None = None,
) -> dict:
    system_prompt = load_prompt(
        SYSTEM_PATH,
        title=state.title,
        synopsis=state.synopsis,
        visual_style=state.visual_style,
        tone_guidelines=state.tone_guidelines,
        world_constraints=format_world_constraints(state),
        narrative_premise=state.narrative_premise,
        locations=format_locations(state),
        characters=format_characters_full(state),
        narrative_memory_target_tokens=config.narrative_memory_target_tokens,
    )
    user_prompt = load_prompt(
        USER_PATH,
        turn_number=state.turn_number,
        long_term_narrative=state.long_term_narrative or "(not yet set)",
        short_term_narrative=state.short_term_narrative or "(not yet set)",
        world_state=format_world_state(state.world_state),
        narrative_memory=state.narrative_memory or "(no memory yet)",
        context_brief=state.context_brief or "(empty)",
        previous_end_frame_description=previous_end_frame(state) or "(turn 1 — no previous frame)",
        recent_history=format_recent_history(state, count=config.context_window_history),
        user_input=state.user_input or "(empty — advance on short_term_narrative)",
    )

    parsed = await call_llm_structured(
        agent="solo",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm=llm,
        config=config,
        logger_obj=interaction_logger,
        turn=state.turn_number,
        max_tokens=config.max_tokens_per_agent,
    )

    if parsed is None:
        return {}

    try:
        solo = SoloResponse(**parsed)
    except ValidationError as exc:
        logger.warning("Solo response validation failed on turn %s: %s", state.turn_number, exc)
        return {}

    # Honor the same pacing gates as Attenborough so both configurations
    # produce comparable commentary streams. Only enforced when the live
    # producer is bookkeeping; benchmark/play loops always pass through
    # the model's commentary verbatim.
    if config.audio_enabled and state.pacing_managed and (
        state.audio_seconds_owed > 0.001
        or state.silence_seconds < config.min_pause_seconds
    ):
        effective_commentary = Commentary(voiceover="")
        interaction_logger.log_event(
            "solo_commentary_hold",
            state.turn_number,
            {"reason": "audio_owed" if state.audio_seconds_owed > 0.001 else "min_pause",
             "audio_seconds_owed": state.audio_seconds_owed,
             "silence_seconds": state.silence_seconds,
             "suppressed_voiceover": solo.commentary.voiceover},
        )
    else:
        effective_commentary = solo.commentary

    # TTS side effect — identical to Attenborough's, since solo owns commentary too.
    audio_path = ""
    if config.audio_enabled and tts is not None and effective_commentary.voiceover:
        result_path = await tts.synthesize(effective_commentary.voiceover, turn=state.turn_number)
        interaction_logger.log_tts(
            turn=state.turn_number,
            voice_id=tts.voice_id,
            text=effective_commentary.voiceover,
            audio_path=result_path,
            success=result_path is not None,
        )
        audio_path = result_path or ""

    new_world_state = _apply_delta(state.world_state, solo.memory_update.world_state_delta)

    update: dict = {
        "current_beat": solo.beat,
        "current_shot": solo.shot,
        "current_commentary": effective_commentary,
        "current_audio_path": audio_path,
        "short_term_narrative": solo.beat.short_term_narrative or state.short_term_narrative,
        "world_state": new_world_state,
        "narrative_memory": solo.memory_update.narrative_memory,
        "context_brief": solo.memory_update.context_brief,
    }
    if solo.beat.long_term_narrative:
        update["long_term_narrative"] = solo.beat.long_term_narrative
    return update
