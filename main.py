"""Entry point for Untitled Project.

Usage:
    python main.py check --config configs/director.yaml                      # setup check, no paid calls
    python main.py play --config configs/director.yaml                       # terminal, turn by turn
    python main.py play --config configs/director.yaml --player web          # the film as text in a browser
    python main.py play --config configs/director.live.yaml --player web     # live LTX-2.5 video in a browser
    python main.py play --config configs/director.live.yaml                  # live video in an ffplay window
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    sys.exit("Untitled Project needs Python 3.10 or newer (3.11+ recommended).")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402

try:
    from dotenv import load_dotenv

    # Populate os.environ from .env before any backend is instantiated.
    # Shell-exported values already in the environment win — override=False.
    load_dotenv(override=False)

    from src.runner import run_live, run_play
    from src.i2v.ltxv import ReferenceRefused
    from src.i2v.shape import UnregisteredShape
    from src.models.config import Config
    from src.models.story import Story
    from src.playback import TerminalPlayer
    from src.preflight import check_setup
    from src.ui.terminal import TerminalUI
except ModuleNotFoundError as exc:  # pragma: no cover - only without the dependencies
    sys.exit(f"Missing Python package {exc.name!r}: run `pip install -r requirements.txt` "
             f"(inside your virtual environment).")

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
    parser = argparse.ArgumentParser(description="Untitled Project: an interactive video generation system")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="Play the film")
    play.add_argument("--config", type=Path, required=True)
    play.add_argument("--story", type=Path, default=DEFAULT_STORY)
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("--player", choices=["terminal", "web"], default=None,
                      help="Live film surface: 'web' serves a browser player that streams the film "
                           "and takes input mid-stream; 'terminal' is an ffplay window plus stdin. "
                           "Either implies the live loop (default: config's `live` flag).")
    play.add_argument("--host", default="127.0.0.1",
                      help="Web player bind address (default 127.0.0.1: this computer only; the player has no login)")
    play.add_argument("--port", type=int, default=8765, help="Web player port (0 = any free port)")
    play.add_argument("--segments", type=int, default=None,
                      help="Live loop: stop after this many segments (default: run until Ctrl+C)")
    play.add_argument("-v", "--verbose", action="store_true")

    check = sub.add_parser("check", help="Check the setup for a config without paid calls")
    check.add_argument("--config", type=Path, default=Path("configs/director.yaml"))
    check.add_argument("--story", type=Path, default=DEFAULT_STORY)
    check.add_argument("-v", "--verbose", action="store_true")

    return parser


def load(args: argparse.Namespace) -> tuple[Config, Story]:
    """The run's config and story, or SystemExit with what is wrong."""
    try:
        config = Config.from_yaml(args.config)
    except Exception as exc:
        raise SystemExit(f"Cannot load the config {args.config}: {exc}") from None
    try:
        story = Story.from_json(args.story)
    except Exception as exc:
        raise SystemExit(f"Cannot load the story {args.story}: {exc}") from None
    return config, story


def cmd_check(args: argparse.Namespace) -> int:
    config, story = load(args)
    print(f"Setup check for {args.config} and {args.story} (no paid calls):")
    findings = check_setup(config, story, probe_server=True)
    for finding in findings:
        print(f"  {finding.level:<8}{finding.message}")
    errors = sum(f.level == "error" for f in findings)
    print(f"{errors} problem(s) to fix before `python main.py play`." if errors else "Ready to play.")
    return 1 if errors else 0


async def cmd_play(args: argparse.Namespace) -> int:
    config, story = load(args)
    errors = [f.message for f in check_setup(config, story, probe_server=False) if f.level == "error"]
    if errors:
        print("Cannot start the film:", *(f"  - {e}" for e in errors), sep="\n", file=sys.stderr)
        print(f"`python main.py check --config {args.config}` checks the whole setup.", file=sys.stderr)
        return 1

    try:
        if args.player is not None or config.live:
            if args.player == "web":
                from src.web import WebPlayer
                player = WebPlayer(host=args.host, port=args.port, seam=config.seam)
            else:
                player = TerminalPlayer()
            await run_live(config=config, story=story, log_dir=args.log_dir,
                           player=player, max_segments=args.segments)
        else:
            await run_play(config=config, story=story, log_dir=args.log_dir, ui=TerminalUI())
    except (UnregisteredShape, ReferenceRefused) as exc:
        # The renderer refused the run before anything rendered.
        print(f"Cannot start the film: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "check":
        sys.exit(cmd_check(args))
    if args.command == "play":
        sys.exit(asyncio.run(cmd_play(args)))


if __name__ == "__main__":
    main()
