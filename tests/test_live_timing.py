"""The live loop renders just in time and lets the viewer cut a beat short.

Once playback runs, a clip's render is fired as late as it safely can
be, and the Director is asked for the next beat only when the current
one has no clip left. A line typed while a clip plays drops the beat's
unrendered clips, the Director writes the next beat from the line at
once, and that beat's first clip is the next one rendered.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from src.eval.runner import run_live
from src.models.config import Config
from src.models.story import Story
from src.playback.player import Player


def _tiny_clip(path: Path, seconds: float = 1.0) -> Path:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi",
                    "-i", f"color=c=white:s=128x80:d={seconds}:r=24",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


class Steer(Player):
    """Plays in real time and posts one line shortly after a given clip starts."""

    def __init__(self, line: str, at_turn: int, after: float) -> None:
        super().__init__()
        self.line, self.at_turn, self.after = line, at_turn, after
        self.turns: list[int] = []
        self.statuses: list[str] = []

    def status(self, text: str) -> None:
        self.statuses.append(text)

    async def play(self, item) -> None:
        self.turns.append(item.turn)
        if item.turn == self.at_turn:
            asyncio.get_running_loop().call_later(self.after, self.push_input, self.line)
        await self._hold(item.duration)


async def test_line_typed_while_a_clip_plays_cuts_the_beat_and_lands_two_clips_later():
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        _tiny_clip(clips / "a.mp4")
        # Replay backend with a 0.3 s "render" of 1 s clips: the mock Director
        # is instant, so the loop has most of each clip to wait for input. A
        # 1 s buffer fires each render about a clip ahead of its air time.
        config = Config(name="timing", graph="director", llm_backend="mock", model="mock",
                        video_enabled=True, clip_seconds=1, startup_clips=0,
                        buffer_seconds=1.0,
                        i2v_backend="replay", i2v_replay_dir=str(clips), i2v_replay_delay=0.3)
        player = Steer("a red cap", at_turn=2, after=0.1)
        state = await run_live(config=config, story=story, log_dir=d, player=player, max_segments=5)
        transcript = next(Path(d).glob("*.md")).read_text()
    inputs = [h.user_input for h in state.history]
    assert player.turns == [1, 2, 3, 4, 5]
    # Clip 2 plays while clip 3 renders; the line arrives 0.1 s in, before
    # clip 4 is rendered, so it drops the rest of the running beat and the
    # Director's next beat opens at 4.
    assert inputs == ["", "", "", "a red cap", ""], inputs
    assert "cut short" in transcript
    assert any(s.startswith("listening for your input") for s in player.statuses), "the loop waited for the viewer"
