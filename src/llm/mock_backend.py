"""Scripted LLM backend for offline runs and tests.

Returns schema-shaped JSON without calling any API, so the whole engine
(world model, validator, compiler, triggers, persistence, transcript)
can be exercised deterministically. The Spark script is keyed on what it
finds in the prompt: the viewer's line, whether a request is still owed,
whether the story is opening. The Echo script returns a notebook and
optional compaction.
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
    return "Earlier suggestions still owed" in _user(messages)


def _owed_segment(messages: list[dict]) -> int:
    m = re.search(r"Earlier suggestions still owed \(deferred\):\n- segment (\d+):", _user(messages))
    return int(m.group(1)) if m else 0


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
    echo_delay: float = 0.0

    def __init__(self, model: str = "mock") -> None:
        self.model = model

    async def generate_structured(self, messages, schema: type[BaseModel], temperature=0.7, max_tokens=1024):
        name = schema.__name__
        if name == "BeatPlan":
            payload = self._beat(messages)
        elif name == "EchoUpdate":
            if self.echo_delay:
                await asyncio.sleep(self.echo_delay)
            payload = self._echo(messages)
        else:
            raise ValueError(f"MockBackend has no script for schema {name!r}")
        return json.dumps(payload), {"prompt": 0, "completion": 0, "total": 0}

    # ── Spark script ───────────────────────────────────────────────

    def _beat(self, messages: list[dict]) -> dict:
        n = _segment_number(messages)
        retry = _corrections_requested(messages)
        line = _viewer_line(messages)
        base = {
            "intent": f"beat from segment {n}",
            "framing": "medium shot, straight-on, eye level",
            "clips": [],
            "intervention": "none",
            "intervention_reason": "",
            "delivered_request": 0,
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
            base["delivered_request"] = _owed_segment(messages)
            base["intervention_reason"] = "the deferred trick happens now"
        elif retry:
            base["intent"] = "the kick"
            base["clips"] = [_clip(
                "He kicks the ball across the void.", "the ball far away",
                on_screen=["lego_man", "soccer_ball"],
                entity_ops=[_op("update", "soccer_ball", state="kicked across the void")],
            )]
        else:
            base["clips"] = [
                _clip(f"Lego Man performs action {n}.", f"end of segment {n}",
                      entity_ops=[_op("update", "lego_man", kind="character", state=f"state {n}")]),
                _clip("He holds still.", f"still after segment {n}"),
            ]
        return base

    # ── Echo script ────────────────────────────────────────────────

    def _echo(self, messages: list[dict]) -> dict:
        user = _user(messages)
        bootstrap = "Trigger: bootstrap" in user
        return {
            "scene_summary": "" if bootstrap else "The minifigure met a soccer ball and kicked it.",
            "act_summary": "" if bootstrap else "Act one: a ball arrives and is mastered.",
            "notebook": {
                "characters": [{"entity": "lego_man", "portrait": "A plain yellow LEGO minifigure."}],
                "alive": "A lone figure in an empty void." if bootstrap else "The figure has discovered play.",
                "seeds": [{"seed": "What else is out here?", "potential": "The void may hold anything."}],
                "intentions": "Let the figure explore.",
                "audience": "",
            },
            "era": None,
            "rationale": "Scripted mock.",
        }
