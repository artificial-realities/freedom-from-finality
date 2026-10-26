"""Entry point.

    python main.py check                     # the setup and your story, without paid calls
    python main.py play                      # your story in the browser: Begin, or Continue
                                             # from the last clip you saw
    python main.py adopt story.incoming      # take back a copy of your story (from the GPU box)

`--config configs/text.yaml` is a testing tool, not for viewers: the story
as text, no GPU, in its own test story (logs/test-story), never the
viewer's.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.app import App
from src.models.config import Config
from src.models.frame_zero import FrameZero
from src.preflight import Finding, check_setup
from src.story_folder import StoryFolder, StoryFolderError, adopt, describe

DEFAULT_CONFIG = Path("configs/video.yaml")
DEFAULT_FRAME_ZERO = Path("frame_zero.json")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_STORY = Path("story")
TEST_STORY = DEFAULT_LOG_DIR / "test-story"
# Where an older FFF kept the story, in a format this one cannot continue.
OLD_ADVENTURE = Path("adventure")
FRAME_ZERO_HELP = "the Frame Zero a new story begins from (read only when it begins)"
STORY_HELP = f"the story folder (default {DEFAULT_STORY}; text mode: {TEST_STORY})"


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

    play = sub.add_parser("play", help="Open your story in the browser: Begin, or Continue where you left it")
    play.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    play.add_argument("--story", type=Path, default=None, help=STORY_HELP)
    play.add_argument("--frame-zero", type=Path, default=DEFAULT_FRAME_ZERO, help=FRAME_ZERO_HELP)
    play.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    play.add_argument("--host", default="127.0.0.1",
                      help="Player bind address (default 127.0.0.1: this computer only; the player has no login)")
    play.add_argument("--port", type=int, default=8765, help="Player port (0 = any free port)")
    play.add_argument("--clips", type=int, default=None,
                      help="End each film after this many clips (default: when you stop it)")
    play.add_argument("-v", "--verbose", action="store_true")

    check = sub.add_parser("check", help="Check the setup and your story without paid calls")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    check.add_argument("--story", type=Path, default=None, help=STORY_HELP)
    check.add_argument("--frame-zero", type=Path, default=DEFAULT_FRAME_ZERO, help=FRAME_ZERO_HELP)
    check.add_argument("-v", "--verbose", action="store_true")

    take = sub.add_parser("adopt", help="Make a copy of your story brought back from another machine yours, "
                                        "if it is the same story and not behind")
    take.add_argument("incoming", type=Path, help="the copy, next to the story folder (story.incoming)")
    take.add_argument("--story", type=Path, default=DEFAULT_STORY)
    take.add_argument("-v", "--verbose", action="store_true")

    return parser


def load_config(args: argparse.Namespace) -> Config:
    """The run's config, or SystemExit with what is wrong."""
    try:
        return Config.from_yaml(args.config)
    except Exception as exc:
        raise SystemExit(f"Cannot load the config {args.config}: {exc}") from None


def story_path(args: argparse.Namespace, config: Config) -> Path:
    """The story a config plays: the viewer's for video; for text, a
    testing tool, a test story that is never the viewer's."""
    if config.video_enabled:
        return args.story or DEFAULT_STORY
    path = args.story or TEST_STORY
    if path.resolve() == DEFAULT_STORY.resolve():
        raise SystemExit(f"Text mode is a testing tool and never plays the viewer's story; "
                         f"it uses {TEST_STORY} (or another --story)")
    return path


def old_adventure(root: Path) -> str:
    """A note when the default story is new but an older FFF's adventure
    is still here: it cannot be continued, and is left as it is."""
    if root == DEFAULT_STORY and not (root / "story.json").exists() and (OLD_ADVENTURE / "adventure.json").exists():
        return (f"{OLD_ADVENTURE}/ holds a story from an older FFF, which this one cannot continue; "
                f"a new story begins in {DEFAULT_STORY}/ ({OLD_ADVENTURE}/ is left as it is)")
    return ""


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args)
    root = story_path(args, config)
    kind = "video" if config.video_enabled else "text"
    print(f"Setup check for {args.config} (no paid calls):")
    try:
        contents = describe(root)
        frame_zero = FrameZero.from_json(root / "frame_zero" / "frame_zero.json" if contents else args.frame_zero)
    except (StoryFolderError, ValueError, OSError) as exc:
        print(f"  {'error':<8}the story in {root} cannot be read: {exc}")
        return 1
    if contents is None:
        found = [Finding("ok", f"no story in {root} yet: the first Begin starts it from {args.frame_zero}")]
        if old_adventure(root):
            found.append(Finding("warning", old_adventure(root)))
    elif contents.kind != kind:
        found = [Finding("error", f"{root} is a {contents.kind} story; {args.config} plays {kind}")]
    else:
        found = [Finding("ok", f"story in {root}: continues after clip {contents.clip}"
                               + ("" if contents.clip else " (nothing played yet)"))]
    findings = found + check_setup(config, frame_zero, probe_server=True)
    for finding in findings:
        print(f"  {finding.level:<8}{finding.message}")
    errors = sum(f.level == "error" for f in findings)
    print(f"{errors} problem(s) to fix before `python main.py play`." if errors else "Ready to play.")
    return 1 if errors else 0


async def cmd_play(args: argparse.Namespace) -> int:
    config = load_config(args)
    root = story_path(args, config)
    note = old_adventure(root)
    if note:
        print(f"Note: {note}.", file=sys.stderr)
    try:
        folder = StoryFolder.open(root, kind="video" if config.video_enabled else "text", frame_zero=args.frame_zero)
    except (StoryFolderError, ValueError, OSError) as exc:
        print(f"Cannot open the story in {root}: {exc}", file=sys.stderr)
        return 1
    try:
        # The page needs none of this; a film does, and is refused without it.
        errors = [f.message for f in check_setup(config, folder.frame_zero, probe_server=False) if f.level == "error"]
        if errors:
            print("The page opens, but no film can play until this is fixed:", *(f"  - {e}" for e in errors),
                  sep="\n", file=sys.stderr)
            print(f"`python main.py check --config {args.config}` checks the whole setup.", file=sys.stderr)
        await App(config=config, folder=folder, log_dir=args.log_dir, host=args.host, port=args.port,
                  max_clips=args.clips).run()
    finally:
        folder.close()
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    try:
        print(adopt(args.incoming, args.story))
    except (StoryFolderError, OSError) as exc:
        print(f"Kept your story as it was: {exc}", file=sys.stderr)
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
    try:
        sys.exit(asyncio.run(cmd_play(args)))
    except KeyboardInterrupt:
        print("\nThe session ended; your story is saved as of the last clip you saw.")


if __name__ == "__main__":
    main()
