"""Solo graph: one node emitting Beat + Shot + Commentary + MemoryUpdate."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agents import run_solo
from src.llm.base import LLMBackend
from src.models.config import Config
from src.state.story_state import StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.util.interaction_logger import InteractionLogger


def build_solo_graph(
    *,
    llm: LLMBackend,
    config: Config,
    interaction_logger: InteractionLogger,
    tts: ElevenLabsTTS | None = None,
):
    graph = StateGraph(StoryState)

    async def solo_node(state: StoryState) -> dict:
        return await run_solo(state, llm, config, interaction_logger, tts=tts)

    graph.add_node("solo", solo_node)
    graph.add_edge(START, "solo")
    graph.add_edge("solo", END)

    return graph.compile()
