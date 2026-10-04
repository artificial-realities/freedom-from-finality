"""Full-loop offline replay: re-drive the engine from recorded plans.

Reads `plans.jsonl` and `showrunner.jsonl` from a session directory and
replays them through the real engine (world model, validator, compiler,
pacing, clip identity) with no LLM. Produces a new session directory
with clips.jsonl, events.jsonl and the world model, so a replay can be
diffed against the original or used to test engine changes offline.

    python scripts/replay_engine.py logs/sessions/<id> --story stories/lego-void \\
        --out /tmp/replay --segments 20

When `--verify` is given, the replayed clips.jsonl is checked against
the original: same clip count, same prompts, same world hashes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.story import Story
from src.story.schemas import BeatPlan, ShowrunnerUpdate


class ReplayBackend(LLMBackend):
    """An LLM backend that returns pre-recorded plans in order."""

    def __init__(self, plans: list[dict], showrunner_updates: list[dict]) -> None:
        self.model = "replay"
        self._plans = list(plans)
        self._sr = list(showrunner_updates)
        self._plan_idx = 0
        self._sr_idx = 0

    async def generate(self, messages, temperature=0.7, max_tokens=1024,
                       *, trace_name=None, trace_metadata=None):
        return "{}", {}

    async def generate_structured(self, messages, schema, temperature=0.7,
                                  max_tokens=1024, *, strict=True,
                                  trace_name=None, trace_metadata=None):
        name = schema.__name__
        if name == "BeatPlan":
            if self._plan_idx >= len(self._plans):
                raise RuntimeError("replay exhausted: no more plans")
            record = self._plans[self._plan_idx]
            self._plan_idx += 1
            return json.dumps(record["beat"]), {"prompt": 0, "completion": 0, "total": 0}
        elif name == "ShowrunnerUpdate":
            if self._sr_idx >= len(self._sr):
                raise RuntimeError("replay exhausted: no more showrunner updates")
            record = self._sr[self._sr_idx]
            self._sr_idx += 1
            return json.dumps(record["update"]), {"prompt": 0, "completion": 0, "total": 0}
        raise ValueError(f"ReplayBackend has no data for schema {name!r}")


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


async def replay(session_dir: Path, story_path: str, out_dir: Path,
                 max_segments: int | None, config_overrides: dict | None = None) -> Path:
    plans = _load_jsonl(session_dir / "plans.jsonl")
    sr_path = session_dir / "showrunner.jsonl"
    sr_updates = _load_jsonl(sr_path) if sr_path.exists() else []

    story = Story.from_json(story_path)
    config_defaults = {
        "name": "replay",
        "graph": "director",
        "llm_backend": "mock",
        "model": "replay",
        "buffer_seconds": 0.0,
    }
    if config_overrides:
        config_defaults.update(config_overrides)
    config = Config(**config_defaults)

    backend = ReplayBackend(plans, sr_updates)

    from src.graph.director_engine import DirectorEngine
    from src.i2v.shape import ShapeRegistry

    out_dir.mkdir(parents=True, exist_ok=True)
    from src.util.timing_log import TimingLog
    timing = TimingLog(None)

    shapes = ShapeRegistry.from_config(config)
    engine = DirectorEngine(
        config=config, llm=backend, shapes=shapes,
        session_dir=out_dir, timing=timing,
    )

    from src.state.story_state import StoryState
    state = StoryState.initialize(story, config.name)

    total = max_segments or len(plans) * 4
    seg = 0
    while seg < total:
        seg += 1
        state.turn_number = seg
        try:
            state = await engine.ainvoke(state)
        except RuntimeError as e:
            if "replay exhausted" in str(e):
                print(f"Replay exhausted at segment {seg}")
                break
            raise
        except Exception as e:
            print(f"Segment {seg} failed: {e}")
            break

    if engine.world is not None:
        engine.world.save(out_dir, rewrite=True)

    return out_dir


def verify(original: Path, replayed: Path) -> bool:
    orig_clips = _load_jsonl(original / "clips.jsonl")
    replay_clips = _load_jsonl(replayed / "clips.jsonl")

    ok = True
    if len(orig_clips) != len(replay_clips):
        print(f"MISMATCH: clip count {len(orig_clips)} vs {len(replay_clips)}")
        ok = False

    for i, (o, r) in enumerate(zip(orig_clips, replay_clips)):
        if o.get("dropped") or r.get("dropped"):
            continue
        if o.get("prompt") != r.get("prompt"):
            print(f"clip {i+1}: prompt differs")
            ok = False
        if o.get("world_hash") != r.get("world_hash"):
            print(f"clip {i+1}: world_hash differs ({o.get('world_hash')} vs {r.get('world_hash')})")
            ok = False

    if ok:
        print(f"Verified: {len(orig_clips)} clips match")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Replay a recorded session through the engine")
    parser.add_argument("session", type=Path, help="path to the original session directory")
    parser.add_argument("--story", default="stories/lego-void", help="story directory")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: <session>/replay)")
    parser.add_argument("--segments", type=int, default=None, help="max segments to replay")
    parser.add_argument("--verify", action="store_true", help="check replayed clips against the original")
    args = parser.parse_args()

    out = args.out or args.session / "replay"
    result = asyncio.run(replay(args.session, args.story, out, args.segments))
    print(f"Replay written to {result}")

    if args.verify:
        verify(args.session, result)


if __name__ == "__main__":
    main()
