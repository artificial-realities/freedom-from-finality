"""Entry point for Artificial Realities.

Usage:
    python main.py play --config configs/director.yaml                       # terminal, turn by turn
    python main.py play --config configs/director.yaml --player web          # the film as text in a browser
    python main.py play --config configs/director.live.yaml --player web     # live LTX-2.5 video in a browser
    python main.py play --config configs/director.live.yaml                  # live video in an ffplay window
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

from src.eval.runner import run_live, run_play  # noqa: E402
from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.playback import TerminalPlayer  # noqa: E402
from src.ui.terminal import TerminalUI  # noqa: E402

DEFAULT_STORY = Path("stories/lego-void")
DEFAULT_LOG_DIR = Path("logs")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        for noisy in ("httpx", "httpcore", "openai"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Artificial Realities runner")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="Play the film")
    play.add_argument("--config", type=Path, required=True)
    play.add_argument("--story", type=Path, default=DEFAULT_STORY)
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("--player", choices=["terminal", "web"], default=None,
                      help="Live film surface: 'web' serves a browser player that streams the film "
                           "and takes input mid-stream; 'terminal' is an ffplay window plus stdin. "
                           "Either implies the live loop (default: config's `live` flag).")
    play.add_argument("--host", default="127.0.0.1", help="Web player bind address")
    play.add_argument("--port", type=int, default=8765, help="Web player port (0 = any free port)")
    play.add_argument("--segments", type=int, default=None,
                      help="Live loop: stop after this many segments (default: run until Ctrl+C)")
    play.add_argument("-v", "--verbose", action="store_true")

    return parser


async def cmd_play(args: argparse.Namespace) -> None:
    story = Story.from_json(args.story)
    config = Config.from_yaml(args.config)

    if args.player is not None or config.live:
        if args.player == "web":
            from src.web import WebPlayer
            player = WebPlayer(host=args.host, port=args.port)
        else:
            player = TerminalPlayer()
        await run_live(config=config, story=story, log_dir=args.log_dir,
                       player=player, max_segments=args.segments)
    else:
        await run_play(config=config, story=story, log_dir=args.log_dir, ui=TerminalUI())


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "play":
        asyncio.run(cmd_play(args))


if __name__ == "__main__":
    main()
