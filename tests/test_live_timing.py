"""The live loop reads the viewer's lines as late as the render allows.

Under pipelining the Director call for segment N+2 overlaps the render of
N+1, which starts when clip N starts playing. If the loop read the lines
at the start of that call, a line typed while watching N would land at
N+3. The late start reads them just before the render finishes, so the
line lands at N+2.
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
    """Plays instantly and posts one line shortly after clip 1 starts."""

    def __init__(self, line: str, after: float) -> None:
        super().__init__(time_scale=1000)
        self.line, self.after = line, after
        self.turns: list[int] = []

    async def play(self, item) -> None:
        self.turns.append(item.turn)
        if item.turn == 1:
            asyncio.get_running_loop().call_later(self.after, self.push_input, self.line)
        await self._hold(item.duration)


async def test_line_typed_while_a_clip_plays_lands_two_segments_later():
    story = Story.from_json("data/story.json")
    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        _tiny_clip(clips / "a.mp4")
        # Replay backend with a 1 s "render": the mock Director is instant,
        # so the loop has most of each render to wait for input.
        config = Config(name="timing", graph="director", llm_backend="mock", model="mock",
                        min_pause_seconds=6.0, observe_frames=False, video_enabled=True,
                        i2v_backend="replay", i2v_replay_dir=str(clips), i2v_replay_delay=1.0)
        player = Steer("a red cap", after=0.1)
        state = await run_live(config=config, story=story, log_dir=d, player=player, max_segments=4)
    inputs = [h.user_input for h in state.history]
    assert player.turns == [1, 2, 3, 4]
    # Clip 1 starts playing when render 2 starts; the line arrives 0.1 s in.
    # Segment 3's Director call is delayed until render 2 is nearly done,
    # so it reads the line. Before the late start it landed at segment 4.
    assert inputs == ["", "", "a red cap", ""], inputs
