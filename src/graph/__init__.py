from src.graph.mas_graph import build_mas_graph
from src.graph.solo_graph import build_solo_graph

__all__ = ["build_mas_graph", "build_solo_graph", "build_graph"]


def build_graph(
    graph_name: str,
    *,
    llm,
    config,
    interaction_logger,
    tts=None,
):
    """Dispatch by config.graph ("solo_graph" | "mas_graph")."""
    key = graph_name.lower()
    if key == "mas_graph":
        return build_mas_graph(
            llm=llm,
            config=config,
            interaction_logger=interaction_logger,
            tts=tts,
        )
    if key == "solo_graph":
        return build_solo_graph(
            llm=llm,
            config=config,
            interaction_logger=interaction_logger,
            tts=tts,
        )
    raise ValueError(f"Unknown graph: {graph_name!r}")
