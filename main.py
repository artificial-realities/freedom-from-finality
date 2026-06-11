"""Entry point for Artificial Realities.

Usage:
    python main.py play --config configs/mas.yaml
    python main.py play --config configs/mas.yaml --scenario data/demo_scenario.json
    python main.py judge <session_id> --annotations <annotations.json>
    python main.py stats <session_id> [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

# Populate os.environ from .env before any backend is instantiated.
# Shell-exported values already in the environment win — override=False.
load_dotenv(override=False)

from src.eval.judge import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    judge_session,
)
from src.eval.runner import run_live, run_live_text, run_play, run_scenario  # noqa: E402
from src.eval.stats import format_stats, session_stats, stats_as_dict  # noqa: E402
from src.llm.anthropic_backend import DEFAULT_JUDGE_MODEL  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.ui.terminal import TerminalUI  # noqa: E402

DEFAULT_STORY = Path("data/story.json")
DEFAULT_LOG_DIR = Path("logs")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx/openai/dashscope log every HTTP request at INFO, which clutters
    # the terminal during runs. Keep them quiet unless the user asked for -v.
    if not verbose:
        for noisy in ("httpx", "httpcore", "openai", "dashscope"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Artificial Realities runner")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="Interactive play (or drive from a scenario file)")
    play.add_argument("--config", type=Path, required=True)
    play.add_argument("--story", type=Path, default=DEFAULT_STORY)
    play.add_argument("--scenario", type=Path, default=None,
                      help="Optional scenario file; if given, runs non-interactively.")
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("-v", "--verbose", action="store_true")

    judge = sub.add_parser("judge", help="Score a finished session via the LLM-as-judge harness")
    judge.add_argument("session_id", help="Session id (matches the on-disk log stem and Langfuse session id).")
    judge.add_argument("--annotations", type=Path, required=True,
                       help="Scenario annotations file grounding the judge rubric.")
    judge.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                       help=f"Anthropic model id for the judge (default: {DEFAULT_JUDGE_MODEL}).")
    judge.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                       help=f"Parallel per-turn judge calls (default {DEFAULT_CONCURRENCY}).")
    judge.add_argument("--output-dir", type=Path, default=DEFAULT_LOG_DIR / "judge")
    judge.add_argument("-v", "--verbose", action="store_true")

    stats = sub.add_parser("stats", help="Operational summary of a finished session (cost/latency/tokens per agent and per turn)")
    stats.add_argument("session_id", help="Session id (matches the on-disk log stem and Langfuse session id).")
    stats.add_argument("--json", action="store_true", help="Emit the full stats as JSON instead of tables.")
    stats.add_argument("-v", "--verbose", action="store_true")

    return parser


async def cmd_play(args: argparse.Namespace) -> None:
    story = Story.from_json(args.story)
    config = Config.from_yaml(args.config)

    if args.scenario is not None:
        await run_scenario(
            config=config,
            story=story,
            scenario_path=args.scenario,
            log_dir=args.log_dir,
        )
    elif config.live and config.video_enabled:
        # Live demo with video: producer + ffplay + stdin reader.
        await run_live(config=config, story=story, log_dir=args.log_dir)
    elif config.live:
        # Live demo without video: continuous loop + Tk popup + stdin reader.
        await run_live_text(config=config, story=story, log_dir=args.log_dir)
    else:
        await run_play(config=config, story=story, log_dir=args.log_dir, ui=TerminalUI())


async def cmd_judge(args: argparse.Namespace) -> None:
    """Score a single finished session and print the aggregate report.

    The detailed scores are pushed to Langfuse (one `Score` per (turn,
    dimension) pair); the JSON summary written under `logs/judge/`
    captures phase-level means so a report can be assembled offline.
    """
    report = await judge_session(
        args.session_id,
        annotations_path=args.annotations,
        judge_model=args.model,
        concurrency=args.concurrency,
        output_dir=args.output_dir,
    )
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


def cmd_stats(args: argparse.Namespace) -> None:
    """Print the operational dashboard for one finished session."""
    stats = session_stats(args.session_id)
    if args.json:
        print(json.dumps(stats_as_dict(stats), indent=2, ensure_ascii=False))
    else:
        print(format_stats(stats))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "play":
        asyncio.run(cmd_play(args))
    elif args.command == "judge":
        asyncio.run(cmd_judge(args))
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
