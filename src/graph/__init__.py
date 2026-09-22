from src.graph.director_engine import DirectorEngine, build_director_engine

__all__ = ["DirectorEngine", "build_director_engine", "build_graph"]


def build_graph(
    graph_name: str,
    *,
    llm,
    config,
    tts=None,
    session_dir=None,
    timing=None,
):
    """Dispatch by config.graph. Only "director" exists today; the key
    stays so a future engine can be A/B'd the same way this one was."""
    key = graph_name.lower()
    if key == "director":
        return build_director_engine(llm=llm, config=config, tts=tts, session_dir=session_dir,
                                     timing=timing)
    raise ValueError(f"Unknown graph: {graph_name!r}")
