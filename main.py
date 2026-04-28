"""Entry point for ClankerStudios.

Usage:
    python main.py play --config configs/mas.yaml
    python main.py play --config configs/solo.yaml --scenario data/test_scenario.json
    python main.py benchmark --scenario data/test_scenario.json
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

# Populate os.environ from .env before any backend is instantiated.
# Shell-exported values already in the environment win — override=False.
load_dotenv(override=False)

from src.eval.runner import run_live, run_live_text, run_play, run_scenario  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.ui.terminal import TerminalUI  # noqa: E402

DEFAULT_STORY = Path("data/story.json")
DEFAULT_SCENARIO = Path("data/test_scenario.json")
DEFAULT_LOG_DIR = Path("logs")
BENCHMARK_CONFIGS = [Path("configs/solo.yaml"), Path("configs/mas.yaml")]


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
    parser = argparse.ArgumentParser(description="ClankerStudios runner")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="Interactive play (or drive from a scenario file)")
    play.add_argument("--config", type=Path, required=True)
    play.add_argument("--story", type=Path, default=DEFAULT_STORY)
    play.add_argument("--scenario", type=Path, default=None,
                      help="Optional scenario file; if given, runs non-interactively.")
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("-v", "--verbose", action="store_true")

    bench = sub.add_parser("benchmark", help="Run both configs against a scenario")
    bench.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    bench.add_argument("--story", type=Path, default=DEFAULT_STORY)
    bench.add_argument("--configs", nargs="+", type=Path, default=BENCHMARK_CONFIGS)
    bench.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    bench.add_argument("-v", "--verbose", action="store_true")

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


async def cmd_benchmark(args: argparse.Namespace) -> None:
    story = Story.from_json(args.story)
    for config_path in args.configs:
        config = Config.from_yaml(config_path)
        logging.info("Running benchmark: config=%s scenario=%s", config.name, args.scenario)
        await run_scenario(
            config=config,
            story=story,
            scenario_path=args.scenario,
            log_dir=args.log_dir,
            ui=None,
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "play":
        asyncio.run(cmd_play(args))
    elif args.command == "benchmark":
        asyncio.run(cmd_benchmark(args))


if __name__ == "__main__":
    main()
