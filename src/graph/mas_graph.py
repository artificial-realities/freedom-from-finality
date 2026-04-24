"""MAS graph: Tolkien → Spielberg → Attenborough → Spock.

Strictly sequential. No retries. Drift is caught on the next turn via
Spock's one-turn-delayed context_brief.
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
from src.util.interaction_logger import InteractionLogger


def build_mas_graph(
    *,
    llm: LLMBackend,
    config: Config,
    interaction_logger: InteractionLogger,
    tts: ElevenLabsTTS | None = None,
):
    graph = StateGraph(StoryState)

    async def tolkien_node(state: StoryState) -> dict:
        return await run_tolkien(state, llm, config, interaction_logger)

    async def spielberg_node(state: StoryState) -> dict:
        return await run_spielberg(state, llm, config, interaction_logger)

    async def attenborough_node(state: StoryState) -> dict:
        return await run_attenborough(state, llm, config, interaction_logger, tts=tts)

    async def spock_node(state: StoryState) -> dict:
        return await run_spock(state, llm, config, interaction_logger)

    graph.add_node("tolkien", tolkien_node)
    graph.add_node("spielberg", spielberg_node)
    graph.add_node("attenborough", attenborough_node)
    graph.add_node("spock", spock_node)

    graph.add_edge(START, "tolkien")
    graph.add_edge("tolkien", "spielberg")
    graph.add_edge("spielberg", "attenborough")
    graph.add_edge("attenborough", "spock")
    graph.add_edge("spock", END)

    return graph.compile()
