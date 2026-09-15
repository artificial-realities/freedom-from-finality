"""Scripted viewer for a live run through the web player.

Polls the player's /state and posts each line to /input when the given
segment starts playing, so a live run can be reproduced without someone
typing in the browser. Default lines and segments are the four steers of
the 2026-09-09 full-combination run (typed during segments 2, 4, 6, 8;
absorbed at 3, 5, 7, 9).

    python scripts/steer.py [--player http://127.0.0.1:8765] [--at 2:"text" ...]
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

DEFAULT = [
    (2, "A soccer ball rolls in and stops at your feet. Kick it."),
    (4, "Cut to a close-up of the ball."),
    (6, "Walk off-screen and come back wearing a red cap."),
    (8, "Take a bow."),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", default="http://127.0.0.1:8765")
    ap.add_argument("--at", action="append", default=[], help='segment:"line", repeatable')
    ap.add_argument("--poll", type=float, default=3.0)
    args = ap.parse_args()
    plan = [(int(a.split(":", 1)[0]), a.split(":", 1)[1]) for a in args.at] or DEFAULT
    pending = sorted(plan)
    while pending:
        try:
            state = requests.get(f"{args.player}/state", timeout=5).json()
        except Exception:
            time.sleep(args.poll)
            continue
        if state.get("ended"):
            print(f"film ended with {len(pending)} lines unsent", flush=True)
            return
        now = (state.get("now") or {}).get("turn")
        while pending and now is not None and now >= pending[0][0]:
            seg, text = pending.pop(0)
            r = requests.post(f"{args.player}/input", json={"text": text}, timeout=5)
            print(f"segment {now}: sent {text!r} -> {r.status_code}", flush=True)
        time.sleep(args.poll)
    print("all lines sent", flush=True)


if __name__ == "__main__":
    sys.exit(main())
