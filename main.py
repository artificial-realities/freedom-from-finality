"""Entry point.

    python main.py check [--config configs/video.yaml]   # the setup, without paid calls
    python main.py play                                  # the film as text, in the browser
    python main.py play --config configs/video.yaml      # the film as video (needs the LTX-2.5 server)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.i2v.ltxv import ReferenceRefused
from src.i2v.shape import RendererNotReady
from src.models.config import Config
from src.models.story import Story
from src.preflight import check_setup
from src.runner import run_live
from src.web import WebPlayer

DEFAULT_CONFIG = Path("configs/text.yaml")
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

    play = sub.add_parser("play", help="Play the film in the browser")
    play.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    play.add_argument("--story", type=Path, default=DEFAULT_STORY)
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("--host", default="127.0.0.1",
                      help="Player bind address (default 127.0.0.1: this computer only; the player has no login)")
    play.add_argument("--port", type=int, default=8765, help="Player port (0 = any free port)")
    play.add_argument("--segments", type=int, default=None, help="Stop after this many clips (default: Ctrl+C)")
    play.add_argument("-v", "--verbose", action="store_true")

    check = sub.add_parser("check", help="Check the setup for a config without paid calls")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
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
        await run_live(config=config, story=story, log_dir=args.log_dir,
                       player=WebPlayer(host=args.host, port=args.port), max_segments=args.segments)
    except (RendererNotReady, ReferenceRefused) as exc:
        # The renderer refused the run before anything rendered.
        print(f"Cannot start the film: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    # .env fills in what the shell has not set; the shell's values win.
    load_dotenv(override=False)
    args = build_parser().parse_args()
    setup_logging(args.verbose)
    if args.command == "check":
        sys.exit(cmd_check(args))
    sys.exit(asyncio.run(cmd_play(args)))


if __name__ == "__main__":
    main()
