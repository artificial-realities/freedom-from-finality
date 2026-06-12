"""Clock-split turn engine (plan 03, candidate B).

Roles stopped being stages in a serial pipeline and became loops on
their own clocks (role mapping: Tolkien=Director, Spielberg=
Cinematographer, Attenborough=Narrator, Spock=Editor):

- **Chunk clock (critical path, 2 LLM calls):** Tolkien (beat) →
  Spielberg (shot). The turn returns as soon as the shot exists so
  rendering can start.
- **Narrator clock:** Attenborough is invoked only when the silence
  floor is open (no audio owed, `silence_seconds` at or above
  `min_pause_seconds`). In audio modes the live producer keeps doing
  measured bookkeeping; otherwise the engine tracks story-time itself,
  so the narrator paces against the same clock in every mode.
- **Editor, async:** Spock (ledger delta + brief, plus the Mem0 write
  in mem0 mode) runs as a background task on a snapshot of the
  finished turn and is joined at the start of the next turn — the
  one-turn-delayed feedback loop made explicit. Drift is still caught
  on the next turn via the brief.

The name `build_mas_graph` and the `graph: "mas_graph"` config key are
kept for compatibility; LangGraph itself is gone (the engine mutates
the live StoryState in place).
"""

from __future__ import annotations

import asyncio
import logging

from src.agents import (
    run_attenborough,
    run_spielberg,
    run_spock,
    run_tolkien,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Commentary
from src.state.story_state import StoryState
from src.tts.elevenlabs import ElevenLabsTTS

logger = logging.getLogger(__name__)


class TurnEngine:
    def __init__(
        self,
        *,
        llm: LLMBackend,
        config: Config,
        tts: ElevenLabsTTS | None = None,
        memory=None,
    ):
        self.llm = llm
        self.config = config
        self.tts = tts
        self.memory = memory
        self._editor_task: asyncio.Task | None = None

    async def ainvoke(self, state: StoryState) -> StoryState:
        """Run one turn. Mutates and returns the same StoryState."""
        await self._join_editor(state)

        # Critical path: beat → shot.
        self._apply(state, await run_tolkien(state, self.llm, self.config,
                                             memory=self.memory))
        self._apply(state, await run_spielberg(state, self.llm, self.config))

        # Narrator clock: the engine decides whether the room is open;
        # the agent only runs (and only chooses) when it is. Last turn's
        # commentary lingers in state, so silent turns must overwrite it.
        spoke = False
        if self._narrator_gate_open(state):
            update = await run_attenborough(
                state, self.llm, self.config, tts=self.tts, memory=self.memory,
            )
            self._apply(state, update)
            spoke = bool(update)
        if not spoke:
            state.current_commentary = Commentary(voiceover="")
            state.current_audio_path = ""
        if not state.pacing_managed:
            self._tick_story_clock(state)

        self._spawn_editor(state)
        return state

    async def drain(self, state: StoryState) -> None:
        """Join the in-flight editor task at session end.

        Ensures the final world state / brief are complete and the last
        Mem0 write lands before the process exits. Call from the
        runner's `finally`, before the Langfuse flush.
        """
        await self._join_editor(state)

    # -- Editor (async, one-turn-delayed) --------------------------------

    async def _join_editor(self, state: StoryState) -> None:
        if self._editor_task is None:
            return
        task, self._editor_task = self._editor_task, None
        try:
            update = await task
        except Exception:
            logger.exception(
                "Editor task failed — continuing without its update",
            )
            return
        self._apply(state, update)

    def _spawn_editor(self, state: StoryState) -> None:
        # Snapshot so the task is immune to the runner mutating the live
        # state (turn_number, user_input, pacing fields) while it runs.
        snapshot = state.model_copy(deep=True)

        async def _editor() -> dict:
            update = await run_spock(snapshot, self.llm, self.config)
            if self.memory is not None and snapshot.current_beat is not None:
                await self.memory.add_turn(
                    turn=snapshot.turn_number,
                    narration=snapshot.current_beat.narration,
                    outcome=snapshot.current_beat.outcome,
                    voiceover=(snapshot.current_commentary.voiceover
                               if snapshot.current_commentary else ""),
                )
            return update

        # Created inside the turn's Langfuse span — the task copies the
        # contextvars, so its generation attributes to the turn it
        # describes even when it completes during the next one.
        self._editor_task = asyncio.create_task(_editor())

    # -- Narrator clock ---------------------------------------------------

    def _narrator_gate_open(self, state: StoryState) -> bool:
        if state.current_beat is None or state.current_shot is None:
            return False
        if state.audio_seconds_owed > 0.001:
            return False
        if not any(h.commentary.voiceover for h in state.history):
            return True  # no line yet — an opening line is welcome
        return state.silence_seconds >= self.config.min_pause_seconds

    def _tick_story_clock(self, state: StoryState) -> None:
        """Silence bookkeeping for modes without a live audio producer.

        Story-time proxy: one turn ≈ one clip of the configured i2v
        duration. A spoken line resets the floor; silence accumulates.
        """
        if state.current_commentary and state.current_commentary.voiceover:
            state.silence_seconds = 0.0
        else:
            state.silence_seconds += (
                state.last_clip_duration or float(self.config.i2v_duration)
            )

    # -- Plumbing ---------------------------------------------------------

    @staticmethod
    def _apply(state: StoryState, update: dict | None) -> None:
        for key, value in (update or {}).items():
            setattr(state, key, value)


def build_mas_graph(
    *,
    llm: LLMBackend,
    config: Config,
    tts: ElevenLabsTTS | None = None,
    memory=None,
) -> TurnEngine:
    return TurnEngine(llm=llm, config=config, tts=tts, memory=memory)
