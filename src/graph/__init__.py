from src.graph.director_engine import build_director_engine
from src.graph.mas_graph import build_mas_graph

__all__ = ["build_director_engine", "build_mas_graph", "build_graph"]


def build_graph(
    graph_name: str,
    *,
    llm,
    config,
    tts=None,
    memory=None,
    session_dir=None,
):
    """Dispatch by config.graph.

    - "director": the two-role Director/Showrunner engine over a typed
      world model (current design).
    - "mas_graph": the legacy four-role clock-split engine, kept for A/B
      comparison.
    """
    key = graph_name.lower()
    if key == "director":
        return build_director_engine(llm=llm, config=config, tts=tts, session_dir=session_dir)
    if key == "mas_graph":
        return build_mas_graph(llm=llm, config=config, tts=tts, memory=memory)
    raise ValueError(f"Unknown graph: {graph_name!r}")
