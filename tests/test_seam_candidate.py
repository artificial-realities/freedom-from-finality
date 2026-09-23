"""The seam design's candidate pieces (AUD-02 / AUD-03 / LTX-12, X-02 and
X-13 only), off by default: the player's overlap mode and the server's
audio ceiling."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from pathlib import Path

from src.playback.player import Playable
from src.web import WebPlayer

_spec = importlib.util.spec_from_file_location("audio_ceiling", Path("server/audio_ceiling.py"))
audio_ceiling = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio_ceiling)


def test_the_ceiling_gain_never_boosts_and_keeps_the_true_peak_down():
    g = audio_ceiling.gain_db
    assert g(-30.0, -10.0, -18.0) == 0.0, "a quiet clip is untouched"
    assert g(-10.0, -12.0, -18.0) == -8.0, "down to the ceiling"
    assert g(-19.0, 0.5, -18.0) == -1.5, "under the ceiling but peaking: trimmed to -1 dBTP"
    assert g(-10.0, 0.0, -18.0) == -8.0, "the ceiling already brings the peak under the limit"
    assert g(None, None, -18.0) == 0.0 and g(float("-inf"), float("-inf"), -18.0) == 0.0, "silence"
    assert audio_ceiling.audio_filter(-3.5, 6.0417) == "apad=whole_dur=6.0417,volume=-3.50dB"


def test_the_ceiling_measures_like_the_audio_check():
    with tempfile.TemporaryDirectory() as d:
        loud = Path(d) / "loud.wav"
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-af", "volume=7", str(loud)], check=True)       # lavfi sine is 1/8 full scale
        lufs, peak = audio_ceiling.measure(str(loud))
        assert -8 < lufs < -2 and -2 < peak < 1, (lufs, peak)
        gain = audio_ceiling.gain_db(lufs, peak, -18.0)
        out = Path(d) / "out.wav"
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", str(loud), "-af", audio_ceiling.audio_filter(gain, 4.0),
                        str(out)], check=True)
        after, after_peak = audio_ceiling.measure(str(out))
        assert abs(after - -18.0) < 0.5 and after_peak <= -1.0, (after, after_peak)


def test_the_player_seam_mode_is_fade_unless_an_experiment_asks():
    assert WebPlayer(port=0).seam == "fade"
    assert WebPlayer(port=0, seam="overlap")._snapshot()["seam"] == "overlap"
    try:
        WebPlayer(port=0, seam="dissolve")
        raise AssertionError("an unknown seam is refused")
    except ValueError:
        pass
    item = Playable(turn=2, path="x.mp4", duration=5.667, overlap_seconds=0.375, speech=True)
    assert item.model_dump()["overlap_seconds"] == 0.375 and Playable(turn=1).overlap_seconds == 0.0
    page = Path("src/web/player.html").read_text()
    assert "let seam = 'fade';" in page and "if (s.seam === 'overlap'" in page, "the page defaults to today's fade"
    source = Path("server/ltx2_server.py").read_text()
    assert 'os.getenv("LTX2_AUDIO_CEILING_LUFS", "")' in source, "the server's ceiling is off unless set"
