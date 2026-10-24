"""Entry point.

    python main.py check                       # the setup and the adventure, without paid calls
    python main.py play                        # your adventure, in the browser: it begins from the
                                               # frame zero the first time and resumes ever after
    python main.py adopt adventure.incoming    # take back a copy of the adventure (from the GPU box)

`--config configs/text.yaml` is a testing tool, not for viewers: the story as
text, no GPU, in its own test adventure (logs/test-adventure), never the
viewer's.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.adventure import Adventure, AdventureError, adopt, describe
from src.i2v.ltxv import ReferenceRefused
from src.i2v.shape import RendererNotReady
from src.models.config import Config
from src.models.story import FrameZero
from src.preflight import Finding, check_setup
from src.runner import run_live
from src.web import WebPlayer

DEFAULT_CONFIG = Path("configs/video.yaml")
DEFAULT_STORY = Path(".")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_ADVENTURE = Path("adventure")
TEST_ADVENTURE = DEFAULT_LOG_DIR / "test-adventure"
STORY_HELP = "the frame zero a new adventure begins from (read only when it begins)"
ADVENTURE_HELP = f"the adventure folder (default {DEFAULT_ADVENTURE}; text mode: {TEST_ADVENTURE})"


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        for noisy in ("httpx", "httpcore", "openai"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freedom from Finality: an interactive video generation system")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="Play your adventure in the browser, from where you left it")
    play.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    play.add_argument("--adventure", type=Path, default=None, help=ADVENTURE_HELP)
    play.add_argument("--story", type=Path, default=DEFAULT_STORY, help=STORY_HELP)
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("--host", default="127.0.0.1",
                      help="Player bind address (default 127.0.0.1: this computer only; the player has no login)")
    play.add_argument("--port", type=int, default=8765, help="Player port (0 = any free port)")
    play.add_argument("--segments", type=int, default=None, help="Stop after this many clips (default: Ctrl+C)")
    play.add_argument("-v", "--verbose", action="store_true")

    check = sub.add_parser("check", help="Check the setup and the adventure without paid calls")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    check.add_argument("--adventure", type=Path, default=None, help=ADVENTURE_HELP)
    check.add_argument("--story", type=Path, default=DEFAULT_STORY, help=STORY_HELP)
    check.add_argument("-v", "--verbose", action="store_true")

    take = sub.add_parser("adopt", help="Make a copy of your adventure brought back from another machine yours, "
                                        "if it is the same adventure and not behind")
    take.add_argument("incoming", type=Path, help="the copy, next to the adventure folder (adventure.incoming)")
    take.add_argument("--adventure", type=Path, default=DEFAULT_ADVENTURE)
    take.add_argument("-v", "--verbose", action="store_true")

    return parser


def load_config(args: argparse.Namespace) -> Config:
    """The run's config, or SystemExit with what is wrong."""
    try:
        return Config.from_yaml(args.config)
    except Exception as exc:
        raise SystemExit(f"Cannot load the config {args.config}: {exc}") from None


def adventure_path(args: argparse.Namespace, config: Config) -> Path:
    """The adventure a config plays: the viewer's for video; for text, a
    testing tool, a test adventure that is never the viewer's."""
    if config.video_enabled:
        return args.adventure or DEFAULT_ADVENTURE
    path = args.adventure or TEST_ADVENTURE
    if path.resolve() == DEFAULT_ADVENTURE.resolve():
        raise SystemExit(f"Text mode is a testing tool and never plays the viewer's adventure; "
                         f"it uses {TEST_ADVENTURE} (or another --adventure)")
    return path


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args)
    root = adventure_path(args, config)
    kind = "video" if config.video_enabled else "text"
    print(f"Setup check for {args.config} (no paid calls):")
    try:
        summary = describe(root)
        story = FrameZero.from_json(root / "origin" / "frame_zero.json" if summary else args.story)
    except (AdventureError, ValueError, OSError) as exc:
        print(f"  {'error':<8}the adventure in {root} cannot be read: {exc}")
        return 1
    if summary is None:
        found = [Finding("ok", f"no adventure in {root} yet: the first play begins it from {args.story}")]
    elif summary.kind != kind:
        found = [Finding("error", f"{root} is a {summary.kind} adventure; {args.config} plays {kind}")]
    else:
        found = [Finding("ok", f"adventure in {root}: resumes after segment {summary.segment}"
                               + ("" if summary.segment else " (nothing played yet)"))]
    findings = found + check_setup(config, story, probe_server=True)
    for finding in findings:
        print(f"  {finding.level:<8}{finding.message}")
    errors = sum(f.level == "error" for f in findings)
    print(f"{errors} problem(s) to fix before `python main.py play`." if errors else "Ready to play.")
    return 1 if errors else 0


async def cmd_play(args: argparse.Namespace) -> int:
    config = load_config(args)
    root = adventure_path(args, config)
    try:
        adventure = Adventure.open(root, kind="video" if config.video_enabled else "text", frame_zero=args.story)
    except (AdventureError, ValueError, OSError) as exc:
        print(f"Cannot open the adventure in {root}: {exc}", file=sys.stderr)
        return 1
    try:
        errors = [f.message for f in check_setup(config, adventure.origin, probe_server=False) if f.level == "error"]
        if errors:
            print("Cannot start the film:", *(f"  - {e}" for e in errors), sep="\n", file=sys.stderr)
            print(f"`python main.py check --config {args.config}` checks the whole setup.", file=sys.stderr)
            return 1
        await run_live(config=config, adventure=adventure, log_dir=args.log_dir,
                       player=WebPlayer(host=args.host, port=args.port), max_segments=args.segments)
    except (RendererNotReady, ReferenceRefused) as exc:
        # The renderer refused the run before anything rendered.
        print(f"Cannot start the film: {exc}", file=sys.stderr)
        return 1
    finally:
        adventure.close()
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    try:
        print(adopt(args.incoming, args.adventure))
    except (AdventureError, OSError) as exc:
        print(f"Kept your adventure as it was: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    # .env fills in what the shell has not set; the shell's values win.
    load_dotenv(override=False)
    args = build_parser().parse_args()
    setup_logging(args.verbose)
    if args.command == "check":
        sys.exit(cmd_check(args))
    if args.command == "adopt":
        sys.exit(cmd_adopt(args))
    sys.exit(asyncio.run(cmd_play(args)))


if __name__ == "__main__":
    main()
