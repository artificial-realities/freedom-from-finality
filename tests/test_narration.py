"""Narration and voices belong to the story (AUD-06, LTX-05), and every
clip takes one render path with no audio spanning (AUD-04)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from src.agents.director import SYSTEM_PATH
from src.models.config import Config
from src.models.story import Narrator, Story, Voice
from src.state.story_state import StoryState
from src.story.compiler import compile_clip, speech
from src.story.context import format_cast, format_gates, format_narrator
from src.story.pacing import voiceover_gate
from src.story.schemas import BeatPlan, ClipSpec, DialogueLine
from src.story.world import WorldModel


def _story(**change) -> Story:
    return Story.from_json("stories/lego-void").model_copy(update=change)


def test_the_narrator_is_the_storys():
    """AUD-06: the Lego blueprint defines its narrator (register, voice,
    language, a 15 s floor); a blueprint from before AUD-06 reads its tone
    guidelines as the register; no tone means no narrator. Nothing in the
    run config describes the narrator any more."""
    lego = Story.from_json("stories/lego-void")
    assert lego.narrator.enabled and lego.narrator.min_pause_seconds == 15
    assert lego.narrator.voice.language == "English" and "documentary" in lego.narrator.voice.description
    assert lego.narrator.tone.startswith("David-Attenborough-style") and lego.tone_guidelines == ""
    assert "no voiceover" not in " ".join(lego.world_constraints), "the blueprint no longer contradicts itself"
    raw = json.loads(Path("stories/lego-void/story.json").read_text())
    raw.pop("narrator")
    old = Story(**{**raw, "tone_guidelines": "A dry documentary voice."})
    assert old.narrator.enabled and old.narrator.tone == "A dry documentary voice." and old.narrator.min_pause_seconds == 0
    silent = Story(**{**raw, "tone_guidelines": ""})
    assert silent.narrator.enabled is False
    assert Narrator(voice="a raspy voice").voice == Voice(description="a raspy voice"), "a plain string is a description"
    for field in ("min_pause_seconds", "voiceover_style"):
        assert field not in Config.model_fields, field
    for cfg in ("configs/director.yaml", "configs/director.live.yaml", "configs/director.replay.yaml"):
        assert "min_pause_seconds" not in Path(cfg).read_text(), cfg


def test_the_gate_truth_table():
    """AUD-04: gate = the story has a narrator and its floor of silence has
    passed; silence starts at the floor; the rendering choice never closes
    it (content is not gated on rendering); no history scan, no audio owed."""
    assert "audio_seconds_owed" not in StoryState.model_fields
    for enabled, floor, silence, expected in (
        (True, 15, 15, True), (True, 15, 14.9, False), (True, 15, 30, True),
        (True, 0, 0, True), (False, 0, 100, False), (False, 15, 15, False),
    ):
        st = StoryState.initialize(_story(narrator=Narrator(enabled=enabled, min_pause_seconds=floor)), config_name="t")
        assert st.silence_seconds == floor, "silence starts at the floor: the film may open on a line"
        st.silence_seconds = silence
        assert voiceover_gate(st) is expected, (enabled, floor, silence)
    gates = format_gates(voiceover_open=False, pacing="p", scene="s", narrator=False)
    assert gates.startswith("Voice-over: this film has no narrator.")
    assert format_gates(voiceover_open=True, pacing="p", scene="s").startswith("Voice-over floor: OPEN")


def test_the_director_reads_the_narrator_from_the_story():
    """AUD-06: the system prompt's narrator section comes from the story:
    the register, the language to write in, the story's guidance; the film
    has no hard-coded language or narrator style."""
    assert "{narrator}" in SYSTEM_PATH.read_text() and "{tone_guidelines}" not in SYSTEM_PATH.read_text()
    lego = Story.from_json("stories/lego-void")
    text = format_narrator(lego.narrator)
    assert text.startswith("Register of the narrator's lines (applies only to the `voiceover` field): David-Attenborough")
    assert "The narrator speaks English: write the line in English." in text
    norsk = Narrator(tone="Tørr og lun.", voice=Voice(language="Norwegian"), guidance="Only between scenes.")
    text = format_narrator(norsk, tone_guidelines="A slow winter drama.")
    assert text.startswith("Tone of the film: A slow winter drama.")
    assert "write the line in Norwegian" in text and "When the narrator speaks in this story: Only between scenes." in text
    assert format_narrator(Narrator(enabled=False)) == "This film has no narrator. Leave `voiceover` empty."


def test_voices_are_the_storys():
    """LTX-05: a character's voice (a string or language, accent, delivery
    and vocal characteristics) travels from the blueprint into the world;
    dialogue is compiled in LTX-2's single-speaker form with the line's
    delivery first, the character's voice after, then language and accent;
    the narrator is described from the story, in its language."""
    raw = json.loads(Path("stories/lego-void/story.json").read_text())
    raw["cast_locked"] = False
    raw["characters"].append({"name": "Mara", "description": "the lighthouse keeper", "speaks": True,
                              "voice": {"description": "a low, hoarse voice", "language": "Norwegian",
                                        "accent": "a soft Bergen accent", "delivery": "quiet"}})
    raw["characters"].append({"name": "Tomas", "description": "her nephew", "voice": "bright and quick"})
    story = Story(**raw)
    world = WorldModel.from_blueprint(title=story.title, characters=story.characters, locations=story.locations,
                                      cast_locked=False)
    mara, tomas = world.entities["mara"], world.entities["tomas"]
    assert mara.voice.language == "Norwegian" and tomas.voice == Voice(description="bright and quick")
    assert world.entities["lego_man"].voice is None
    cast = format_cast(world)
    assert "Voice: a low, hoarse voice; speaks Norwegian; a soft Bergen accent; usually quiet." in cast
    assert "Never speaks" in cast, "the mute character keeps its rule"

    beat = BeatPlan(intent="talk", framing="medium shot", clips=[ClipSpec(
        action="Mara turns to Tomas.", end_state="they face each other", on_screen=["mara", "tomas"],
        dialogue=[DialogueLine(speaker="mara", line='It went out at "two".', delivery="hoarse, urgent"),
                  DialogueLine(speaker="tomas", line="How many?")])])
    prompt = compile_clip(beat, 0, world, visual_style="", narration_mode="ltx", segment_no=0)
    assert ("Mara says in a hoarse, urgent, low, hoarse voice, in Norwegian with a soft Bergen accent, "
            "\"It went out at 'two'.\"") in prompt, prompt
    assert 'Tomas says in a bright and quick voice, "How many?"' in prompt
    assert speech("Ola", "Hei.", voice=Voice(delivery="calm")) == 'Ola says in a calm voice, "Hei."'
    assert speech("Ola", "Hei.") == 'Ola says, "Hei."'

    beat.voiceover = "Some nights the sea keeps its own count."
    nar = Narrator(voice=Voice(description="a deep, calm voice", language="Norwegian"))
    prompt = compile_clip(beat, 0, world, visual_style="", narration_mode="ltx", narrator=nar, segment_no=0)
    assert "A narrator's voice-over (Norwegian, deep, calm) says:" in prompt
    bare = compile_clip(beat, 0, world, visual_style="", narration_mode="ltx", narrator=Narrator(), segment_no=0)
    assert 'A narrator\'s voice-over says: "Some nights' in bare and "English" not in bare, "no language assumed"


def test_spanning_and_the_serial_path_are_gone():
    """AUD-04: no concat-and-span muxing, no serial branch, no audio owed."""
    import src.playback as playback
    import src.eval.runner as runner
    assert not hasattr(playback, "concat_videos_and_mux_audio")
    source = Path(runner.__file__).read_text()
    for gone in ("pending_span", "audio_seconds_owed", "mux_inline", "Serial path", "_pipeline_ok"):
        assert gone not in source, gone


def _tone(path: Path, seconds: float) -> str:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    str(path)], check=True)
    return str(path)


class FakeTTS:
    """Speaks each line for a scripted length (a sine tone), in order."""

    def __init__(self, folder: Path, lengths: list[float]) -> None:
        self.folder, self.lengths, self.lines = folder, list(lengths), []

    async def synthesize(self, text: str, turn: int) -> str | None:
        self.lines.append((turn, text))
        return _tone(self.folder / f"tts_{turn}.wav", self.lengths.pop(0) if self.lengths else 0.5)


async def test_a_tts_line_takes_the_one_render_path():
    """AUD-04 through the live loop: a TTS line that fits its clip is mixed
    into that clip in the background render task and queued like any
    other; a line longer than its clip is rejected (noted), never spanned
    over the next clips; every clip is fired and queued once, in order."""
    from src.eval import runner
    from tests.test_engine import Scripted, _config
    story = Story.from_json("stories/lego-void")
    with tempfile.TemporaryDirectory() as d:
        clips = Path(d) / "clips"
        clips.mkdir()
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "color=c=white:s=128x80:d=1:r=24",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-shortest", "-pix_fmt", "yuv420p",
                        str(clips / "a.mp4")], check=True)
        tts = FakeTTS(Path(d), [0.5, 3.0])
        config = _config(video_enabled=True, clip_seconds=1, i2v_backend="replay", i2v_replay_dir=str(clips),
                         narration="tts", audio_enabled=True)
        story = story.model_copy(update={"narrator": story.narrator.model_copy(update={"min_pause_seconds": 0})})
        with mock.patch.object(runner, "_maybe_tts", lambda *a, **k: tts):
            await runner.run_live(config=config, story=story, log_dir=d, max_segments=6,
                                  player=Scripted(lines={1: "A soccer ball appears at your feet."}))
        transcript = next(Path(d).glob("*.md")).read_text()
        records = [json.loads(x) for x in next((Path(d) / "sessions").iterdir()).joinpath("timing.jsonl").read_text().splitlines()]
        muxed = sorted(p.name for p in (Path(d) / "video").glob("*_muxed.mp4"))
    spoken = [turn for turn, _ in tts.lines]
    assert len(spoken) >= 2, tts.lines
    assert muxed == [f"turn_{spoken[0]:04d}_muxed.mp4"], (muxed, spoken)
    assert "tts: the line runs 3.0 s, longer than the 1 s clip; not rendered" in transcript
    fires = [r["turn"] for r in records if r["event"] == "fire"]
    queued = [r["turn"] for r in records if r["event"] == "enqueued"]
    assert fires == queued == [1, 2, 3, 4, 5, 6], (fires, queued)
