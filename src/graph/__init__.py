from src.graph.mas_graph import build_mas_graph

__all__ = ["build_mas_graph", "build_graph"]


def build_graph(
    graph_name: str,
    *,
    llm,
    config,
    tts=None,
):
    """Dispatch by config.graph ("mas_graph")."""
    key = graph_name.lower()
    if key == "mas_graph":
        return build_mas_graph(llm=llm, config=config, tts=tts)
    raise ValueError(f"Unknown graph: {graph_name!r}")
