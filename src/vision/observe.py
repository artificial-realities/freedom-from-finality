"""Frame observation: check what the renderer actually produced.

After a clip renders, two frames (middle and end) go to a vision model
together with the sheets of the entities that were supposed to be on
screen and the visual style. The result is a structured `Observation`:
per-entity match and deviations, a background check, and a 0-3 drift
score. The engine lands it in the world model as visual memory, shows it
to the Director as the "Picture" section, and above a threshold forces
the next segment to open with a cut so the renderer re-seeds from the
reference. Runs off the critical path, like the Showrunner.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
from pathlib import Path

from pydantic import ValidationError

from src.llm.base import LLMBackend
from src.models.config import Config
from src.story.schemas import Observation
from src.story.world import WorldModel
from src.util.json_sanitizer import parse_structured_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the continuity supervisor on a generated film. You are shown frames from the clip that was just rendered, the description of how each character or prop on screen is supposed to look, and the intended visual style. Compare what you see with what was intended.

Be literal and specific. Report a deviation when a colour, a shape, a printed face, hair, clothing, a prop's form, or the background differs from the description. Ignore pose, motion blur, shading and reflections from the lighting. Score drift 0 when everything matches, 1 for a small deviation a viewer would barely notice, 2 for a clear deviation (a changed printed face, added or missing hair, a wrong colour on one part, a background that is no longer the described one), 3 only when the subject is a different character or the setting is a different place from what was described.

Return one JSON object: `entities` (one entry per expected entity with `entity` id, `visible`, `matches`, `deviations`), `background_ok`, `background_note`, `drift` (0-3), `summary` (one sentence)."""


def _duration(video_path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def extract_frames(video_path: str, positions: tuple[float, ...] = (0.5, 0.98), width: int = 512) -> list[bytes]:
    """PNG bytes for frames at the given fractions of the clip's duration."""
    duration = _duration(video_path)
    frames: list[bytes] = []
    for pos in positions:
        at = max(0.0, duration * pos - 0.04) if duration else 0.0
        proc = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-ss", f"{at:.3f}", "-i", video_path, "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-f", "image2", "-vcodec", "png", "pipe:1"],
            capture_output=True,
        )
        if proc.returncode == 0 and proc.stdout:
            frames.append(proc.stdout)
    return frames


def _sheet(world: WorldModel, expected_ids: list[str]) -> str:
    lines = []
    for eid in expected_ids:
        ent = world.resolve(eid)
        if ent is None:
            continue
        lines.append(f"- `{ent.id}` ({ent.kind}) {ent.name}: {ent.render_look()}")
    return "\n".join(lines) or "(no entities expected on screen)"


async def observe_clip(
    *,
    video_path: str,
    segment: int,
    world: WorldModel,
    expected_ids: list[str],
    visual_style: str,
    llm: LLMBackend,
    config: Config,
) -> Observation | None:
    frames = await asyncio.to_thread(extract_frames, video_path)
    if not frames:
        logger.warning("observe: no frames could be extracted from %s", video_path)
        return None
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"Segment {segment}. Frames from the middle and the end of the clip follow.\n\n"
            f"Expected on screen:\n{_sheet(world, expected_ids)}\n\n"
            f"Intended visual style: {' '.join(visual_style.split())}\n\n"
            "Compare and return the JSON object."
        ),
    }]
    for png in frames:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "low",
        }})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    raw, _usage = await llm.generate_structured(
        messages=messages, schema=Observation, strict=True, temperature=0.0,
        max_tokens=600, trace_name="observe",
        trace_metadata={"turn": segment, "config": config.name, "agent": "observe"},
    )
    parsed = parse_structured_response(raw)
    if parsed is None:
        logger.warning("observe: structured parse failed for segment %s", segment)
        return None
    try:
        obs = Observation(**parsed)
    except ValidationError as exc:
        logger.warning("observe: validation failed for segment %s: %s", segment, exc)
        return None
    obs.segment = segment
    return obs
