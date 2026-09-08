"""Scripted LLM backend for offline runs and tests.

Returns schema-shaped JSON without calling any API, so the whole engine
(world model, validator, compiler, triggers, persistence, transcript)
can be exercised deterministically. The script is keyed on the schema
name and the segment number it finds in the prompt; it deliberately
includes proposals the validator must reject (a second character in a
locked cast, dialogue from a mute character) to exercise the re-ask.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from src.llm.base import LLMBackend


def _segment_number(messages: list[dict]) -> int:
    user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    m = re.search(r"^Segment (\d+)", user, re.M)
    return int(m.group(1)) if m else 0


def _corrections_requested(messages: list[dict]) -> bool:
    user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    return "## Corrections required" in user


def _floor_open(messages: list[dict]) -> bool:
    user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    return "Voice-over floor: OPEN" in user


class MockBackend(LLMBackend):
    def __init__(self, model: str = "mock") -> None:
        self.model = model
        self.calls: list[dict] = []

    async def generate(self, messages, temperature=0.7, max_tokens=1024, *, trace_name=None, trace_metadata=None):
        self.calls.append({"name": trace_name, "schema": None})
        return "{}", {}

    async def generate_structured(self, messages, schema: type[BaseModel], temperature=0.7, max_tokens=1024, *, strict=True, trace_name=None, trace_metadata=None):
        self.calls.append({"name": trace_name, "schema": schema.__name__})
        name = schema.__name__
        if name == "Segment":
            payload = self._segment(messages)
        elif name == "ShowrunnerUpdate":
            payload = self._showrunner(messages)
        elif name == "Observation":
            payload = {
                "segment": 0,
                "entities": [{"entity": "lego_man", "visible": True, "matches": False,
                              "deviations": "green head with black hair"}],
                "background_ok": False, "background_note": "grey speckled floor under a spotlight",
                "drift": 3, "summary": "The figure has drifted from the sheet and the void is grey.",
            }
        else:
            raise ValueError(f"MockBackend has no script for schema {name!r}")
        return json.dumps(payload), {"prompt": 0, "completion": 0, "total": 0}

    # ── Director script ─────────────────────────────────────────────

    def _segment(self, messages: list[dict]) -> dict:
        n = _segment_number(messages)
        retry = _corrections_requested(messages)
        vo_open = _floor_open(messages)
        base = {
            "narration": f"Segment {n}: the minifigure does something specific and physical.",
            "outcome": f"Outcome of segment {n}.",
            "next_direction": f"Direction after segment {n}.",
            "shots": [{
                "transition": "continue",
                "framing": "medium shot, straight-on, eye level",
                "camera_motion": "static",
                "action_beats": [f"Lego Man performs action {n}.", "He looks up."],
                "on_screen": ["lego_man"],
                "ambient_sound": "a soft plastic click as his feet touch the floor",
                "end_state": f"Lego Man standing, end of segment {n}",
                "duration_seconds": 5,
            }],
            "dialogue": [],
            "voiceover": "",
            "entity_ops": [],
            "thread_ops": [],
            "intervention": "none",
            "intervention_reason": "",
            "scene_done": False,
            "needs_replan": False,
        }
        if n == 1 and vo_open:
            base["voiceover"] = "From the stillness, he stirs."
        if n == 2:
            base["entity_ops"] = [{"op": "introduce", "entity": "soccer ball", "kind": "prop",
                                   "description": "a black-and-white studded LEGO soccer ball",
                                   "status": "at his feet", "location": "", "holder": "", "present": True}]
            base["shots"][0]["on_screen"] = ["lego_man", "soccer_ball"]
            base["thread_ops"] = [{"op": "open", "thread": "the soccer ball wants to be kicked", "kind": "setup"}]
            base["intervention"] = "absorbed"
            base["intervention_reason"] = "the ball arrives as asked"
        if n == 3 and not retry:
            # Two hard violations: a new character in a locked cast, and a
            # line of dialogue from a character who never speaks.
            base["entity_ops"] = [{"op": "introduce", "entity": "Lego Woman", "kind": "character",
                                   "description": "a second minifigure", "status": "", "location": "",
                                   "holder": "", "present": True}]
            base["dialogue"] = [{"speaker": "lego_man", "line": "Hello!", "delivery": ""}]
        if n == 3 and retry:
            base["entity_ops"] = [{"op": "update", "entity": "soccer_ball", "kind": "prop", "description": "",
                                   "status": "kicked across the void", "location": "", "holder": "", "present": True}]
            base["thread_ops"] = [{"op": "pay_off", "thread": "t1", "kind": "setup"}]
            base["voiceover"] = "A kick, at last." if vo_open else ""
        if n == 4:
            base["shots"] = [
                dict(base["shots"][0], transition="cut", framing="close-up on the ball", duration_seconds=3,
                     on_screen=["soccer_ball"]),
                dict(base["shots"][0], transition="continue", framing="wide shot", duration_seconds=4,
                     on_screen=["lego_man", "soccer_ball"]),
            ]
            base["entity_ops"] = [{"op": "update", "entity": "soccer_ball", "kind": "prop", "description": "",
                                   "status": "held overhead", "location": "", "holder": "lego_man", "present": True}]
            base["intervention"] = "deferred"
            base["intervention_reason"] = "the trick needs a run-up first"
        if n == 5:
            base["entity_ops"] = [{"op": "update", "entity": "soccer_ball", "kind": "prop", "description": "",
                                   "status": "rolled out of frame", "location": "", "holder": "nobody", "present": False}]
            base["scene_done"] = True
            base["intervention"] = "absorbed"
            base["intervention_reason"] = "the deferred trick happens now"
        if n >= 6:
            base["entity_ops"] = [{"op": "update", "entity": "lego_man", "kind": "character", "description": "",
                                   "status": f"status {n}", "location": "", "holder": "", "present": True}]
        return base

    # ── Showrunner script ───────────────────────────────────────────

    def _showrunner(self, messages: list[dict]) -> dict:
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        bootstrap = "Trigger: bootstrap" in user
        compact = "compaction pass" in user
        return {
            "scene_summary": "" if bootstrap else "The minifigure met a soccer ball and kicked it.",
            "act_summary": "" if bootstrap else "Act one: a ball arrives and is mastered.",
            "entity_notes": [] if bootstrap else [{"entity": "lego_man", "note": "He is proud of the kick."}],
            "threads_to_drop": [],
            "arc": ["Wake and discover play.", "Escalating tricks with accumulating props.", "A grand finale with everything."],
            "current_act": 0 if bootstrap else 1,
            "replan": not compact,
            "next_scene": {
                "goal": "Open on the minifigure waking and finding a first thing to do." if bootstrap else "Combine two props in a trick.",
                "tension": "Nothing has happened yet." if bootstrap else "The trick keeps failing.",
                "exit_condition": "He is engaged with a prop." if bootstrap else "A trick lands or fails spectacularly.",
                "intensity": "low" if bootstrap else "medium",
                "candidate_beats": ["A prop appears.", "He tests it."],
            },
            "rationale": "Scripted mock.",
        }
