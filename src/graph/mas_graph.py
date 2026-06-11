"""MAS graph: Tolkien → Spielberg → Attenborough → Spock (→ memory write).

Strictly sequential. No retries. Drift is caught on the next turn via
Spock's one-turn-delayed context_brief.

When a Mem0 store is wired in (config.memory_backend == "mem0"), a final
node persists this turn's beat to episodic memory after Spock — the write
lands before the next turn's retrieval, preserving the one-turn-delay
contract.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agents import (
    run_attenborough,
    run_spielberg,
    run_spock,
    run_tolkien,
)
from src.llm.base import LLMBackend
from src.models.config import Config
from src.state.story_state import StoryState
from src.tts.elevenlabs import ElevenLabsTTS


def build_mas_graph(
    *,
    llm: LLMBackend,
    config: Config,
    tts: ElevenLabsTTS | None = None,
    memory=None,
):
    graph = StateGraph(StoryState)

    async def tolkien_node(state: StoryState) -> dict:
        return await run_tolkien(state, llm, config, memory=memory)

    async def spielberg_node(state: StoryState) -> dict:
        return await run_spielberg(state, llm, config)

    async def attenborough_node(state: StoryState) -> dict:
        return await run_attenborough(state, llm, config, tts=tts, memory=memory)

    async def spock_node(state: StoryState) -> dict:
        return await run_spock(state, llm, config)

    graph.add_node("tolkien", tolkien_node)
    graph.add_node("spielberg", spielberg_node)
    graph.add_node("attenborough", attenborough_node)
    graph.add_node("spock", spock_node)

    graph.add_edge(START, "tolkien")
    graph.add_edge("tolkien", "spielberg")
    graph.add_edge("spielberg", "attenborough")
    graph.add_edge("attenborough", "spock")

    if memory is not None:
        async def memory_write_node(state: StoryState) -> dict:
            if state.current_beat is not None:
                await memory.add_turn(
                    turn=state.turn_number,
                    narration=state.current_beat.narration,
                    outcome=state.current_beat.outcome,
                    voiceover=(state.current_commentary.voiceover
                               if state.current_commentary else ""),
                )
            return {}

        graph.add_node("memory_write", memory_write_node)
        graph.add_edge("spock", "memory_write")
        graph.add_edge("memory_write", END)
    else:
        graph.add_edge("spock", END)

    return graph.compile()
