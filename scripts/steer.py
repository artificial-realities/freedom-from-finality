"""Scripted viewer for a live run through the web player.

Polls the player's /state and posts each line to /input when the given
segment starts playing, so a live run can be reproduced without someone
typing in the browser. Default lines and segments are the four steers of
the 2026-09-09 full-combination run (typed during segments 2, 4, 6, 8;
absorbed at 3, 5, 7, 9).

    python scripts/steer.py [--player http://127.0.0.1:8765] [--at 2:"text" ...] [--at 14+2.3:"text"]

`SEG+SECONDS:text` sends the line that many seconds after segment SEG
started playing (the player's `started_at`); plain `SEG:text` sends it as
soon as SEG is seen playing. `SEG@SECONDS:text` (with `--timing`, the run's
timing.jsonl) times it against a render's fire instead: once SEG plays, at
the next announced fire (`fire_due`) plus SECONDS (negative: before it). A
live run wants a third of its lines within 1 s of a fire (RUN-04); the fire
moves with the beat, so a fixed offset into a segment does not find it.
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


def next_fire(timing: str, *, after: float) -> float | None:
    """The wall time of the earliest announced fire (`fire_due`) still after
    `after`, from the run's timing log; the latest announcement per turn
    counts."""
    import glob
    import json
    paths = sorted(glob.glob(timing)) if timing else []
    if not paths:
        return None
    due: dict[int, float] = {}
    for line in open(paths[-1], encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "fire_due":
            due[r["turn"]] = r["wall"] + r["in_s"]
    ahead = [t for t in due.values() if t > after]
    return min(ahead) if ahead else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", default="http://127.0.0.1:8765")
    ap.add_argument("--at", action="append", default=[], help='segment:"line", repeatable')
    ap.add_argument("--poll", type=float, default=3.0)
    ap.add_argument("--timing", default="", help="the run's timing.jsonl (or a glob), for SEG@SECONDS lines")
    args = ap.parse_args()

    def parse(spec: str) -> tuple[int, float, str, bool]:
        head, text = spec.split(":", 1)
        if "@" in head:
            seg, _, offset = head.partition("@")
            return int(seg), float(offset), text, True
        seg, _, offset = head.partition("+")
        return int(seg), float(offset or 0.0), text, False

    plan = [parse(a) for a in args.at] or [(seg, 0.0, text, False) for seg, text in DEFAULT]
    pending = sorted(plan)
    targets: dict[int, float] = {}
    while pending:
        try:
            state = requests.get(f"{args.player}/state", timeout=5).json()
        except Exception:
            time.sleep(args.poll)
            continue
        if state.get("ended"):
            print(f"film ended with {len(pending)} lines unsent", flush=True)
            return
        playing = state.get("now") or {}
        now, started = playing.get("turn"), playing.get("started_at")
        while pending and now is not None and now >= pending[0][0]:
            seg, offset, text, at_fire = pending[0]
            if at_fire:
                # The fire is chosen once, when the line becomes due (with room
                # to send before it), and kept: later lookups would move on.
                if seg not in targets:
                    due = next_fire(args.timing, after=time.time() + max(-offset, 0.0) + args.poll)
                    if due is None:
                        break                 # no fire announced yet
                    targets[seg] = due
                if time.time() < targets[seg] + offset:
                    break                     # its moment is still ahead
            elif now == seg and offset and started and time.time() < started + offset:
                break                         # not yet: this segment's offset is still ahead
            pending.pop(0)
            r = requests.post(f"{args.player}/input", json={"text": text}, timeout=5)
            late = time.time() - started if started else float("nan")
            print(f"segment {now} +{late:.2f} s: sent {text!r} -> {r.status_code}", flush=True)
        time.sleep(args.poll)
    print("all lines sent", flush=True)


if __name__ == "__main__":
    sys.exit(main())
