"""Scripted LLM backend for offline runs and tests.

Returns schema-shaped JSON without calling any API, so the whole engine
(world model, validator, compiler, triggers, persistence, transcript)
can be exercised deterministically. The Director script is keyed on
what it finds in the prompt: the viewer's line, whether a request is
still owed, which threads are open, whether the film is opening. It
deliberately includes proposals the validator must reject (a second
copy of the protagonist, a line spoken by a prop) to exercise the
re-ask.
"""

from __future__ import annotations

import asyncio
import json
import re

from pydantic import BaseModel

from src.llm.base import LLMBackend


def _user(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")


def _segment_number(messages: list[dict]) -> int:
    m = re.search(r"^Segment (\d+)", _user(messages), re.M)
    return int(m.group(1)) if m else 0


def _corrections_requested(messages: list[dict]) -> bool:
    return "## Corrections required" in _user(messages)


def _viewer_line(messages: list[dict]) -> str:
    m = re.search(r'Viewer input for this beat: "(.*?)"', _user(messages))
    return m.group(1) if m else ""


def _owed(messages: list[dict]) -> bool:
    return "Earlier requests still owed" in _user(messages)


def _owed_segment(messages: list[dict]) -> int:
    m = re.search(r"Earlier requests still owed \(deferred\):\n- segment (\d+):", _user(messages))
    return int(m.group(1)) if m else 0


def _thread_open(messages: list[dict], tid: str) -> bool:
    return f"- `{tid}` [" in _user(messages)


def _op(op: str, entity: str, **over) -> dict:
    base = {"op": op, "entity": entity, "kind": "prop", "description": "", "who": "",
            "voice": "", "state": "", "location": "", "present": None}
    base.update(over)
    return base


def _clip(action: str, end_state: str, **over) -> dict:
    base = {"action": action, "end_state": end_state, "camera_motion": "static",
            "ambient_sound": "a soft plastic click as his feet touch the floor",
            "on_screen": ["lego_man"], "dialogue": [], "entity_ops": []}
    base.update(over)
    return base


class MockBackend(LLMBackend):
    # Seconds a Showrunner call takes (class-level so a test can set it):
    # the engine must never make a segment wait for it.
    showrunner_delay: float = 0.0

    def __init__(self, model: str = "mock") -> None:
        self.model = model

    async def generate_structured(self, messages, schema: type[BaseModel], temperature=0.7, max_tokens=1024):
        name = schema.__name__
        if name == "BeatPlan":
            payload = self._beat(messages)
        elif name == "ShowrunnerUpdate":
            if self.showrunner_delay:
                await asyncio.sleep(self.showrunner_delay)
            payload = self._showrunner(messages)
        else:
            raise ValueError(f"MockBackend has no script for schema {name!r}")
        return json.dumps(payload), {"prompt": 0, "completion": 0, "total": 0}

    # ── Director script ─────────────────────────────────────────────

    def _beat(self, messages: list[dict]) -> dict:
        n = _segment_number(messages)
        retry = _corrections_requested(messages)
        line = _viewer_line(messages)
        base = {
            "intent": f"beat from segment {n}",
            "framing": "medium shot, straight-on, eye level",
            "clips": [],
            "thread_ops": [],
            "intervention": "none",
            "intervention_reason": "",
            "perspective": "",
            "delivered_request": 0,
            "scene_done": False,
            "needs_replan": False,
        }
        if n == 1:
            base["intent"] = "he wakes and looks around"
            base["clips"] = [
                _clip("Lego Man performs action 1. He looks up.", "Lego Man standing, looking up",
                      entity_ops=[_op("update", "lego_man", kind="character",
                                      state="standing centre frame, looking around")],
                      dialogue=[{"speaker": "lego_man", "line": "Hello? Anyone?", "delivery": "quiet"}]),
                _clip("He holds still. Nothing else happens.", "Lego Man standing still"),
            ]
        elif "soccer ball" in line:
            base["intent"] = "a ball arrives"
            base["clips"] = [_clip(
                "A soccer ball rolls in and stops at his feet.", "the ball at his feet",
                on_screen=["lego_man", "soccer_ball"],
                entity_ops=[_op("introduce", "soccer ball", description="a black-and-white studded LEGO soccer ball",
                                state="at his feet", present=True)],
            )]
            base["thread_ops"] = [{"op": "open", "thread": "the soccer ball wants to be kicked", "kind": "setup"}]
            base["intervention"] = "absorbed"
            base["intervention_reason"] = "the ball arrives as asked"
        elif "trick" in line:
            base["intent"] = "he lifts the ball"
            base["clips"] = [_clip(
                "He lifts the ball overhead.", "the ball held overhead",
                on_screen=["lego_man", "soccer_ball"],
                entity_ops=[_op("update", "soccer_ball", state="held overhead by Lego Man")],
            )]
            base["intervention"] = "deferred"
            base["intervention_reason"] = "the trick needs a run-up first"
        elif _owed(messages):
            base["intent"] = "the trick, and the ball leaves"
            base["clips"] = [_clip(
                "He flips the ball up and it rolls out of frame.", "the floor empty",
                entity_ops=[_op("update", "soccer_ball", state="rolled out of frame", present=False)],
            )]
            base["scene_done"] = True
            base["delivered_request"] = _owed_segment(messages)
            base["intervention_reason"] = "the deferred trick happens now"
        elif _thread_open(messages, "t1") and not retry:
            # Two hard violations: a second copy of the protagonist, and a
            # line spoken by a prop.
            base["intent"] = "a double"
            base["clips"] = [_clip(
                "A second Lego Man walks in and the ball greets him.", "two figures",
                entity_ops=[_op("introduce", "Lego Man 2", kind="character", description="another plain minifigure",
                                present=True)],
                dialogue=[{"speaker": "soccer_ball", "line": "Hello!", "delivery": ""}],
            )]
        elif _thread_open(messages, "t1") and retry:
            base["intent"] = "the kick"
            base["clips"] = [_clip(
                "He kicks the ball across the void.", "the ball far away",
                on_screen=["lego_man", "soccer_ball"],
                entity_ops=[_op("update", "soccer_ball", state="kicked across the void")],
            )]
            base["thread_ops"] = [{"op": "pay_off", "thread": "t1", "kind": "setup"}]
        else:
            base["clips"] = [
                _clip(f"Lego Man performs action {n}.", f"end of segment {n}",
                      entity_ops=[_op("update", "lego_man", kind="character", state=f"state {n}")]),
                _clip("He holds still.", f"still after segment {n}"),
            ]
        return base

    # ── Showrunner script ───────────────────────────────────────────

    def _showrunner(self, messages: list[dict]) -> dict:
        user = _user(messages)
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
