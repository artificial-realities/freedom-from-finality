"""`scripts/replay_compiler.py` and `scripts/latency_probe.py --warm` (RUN-13,
part d). A mock run's Director outputs are captured, replayed through the
compiler and compared with the transcript the engine wrote; the probe's
warm mode runs against an in-process fake LTX server with the real
response headers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import yaml
from aiohttp import web

from src.eval.runner import run_live
from src.llm.mock_backend import MockBackend, _segment_number
from src.models.config import Config
from src.models.story import Story
from src.playback.player import Player
from src.story.compiler import compile_clip

REPO = Path(__file__).resolve().parent.parent
STORY = REPO / "stories" / "lego-void"
# The comparison's mechanics against a fixed record: session A's (the canonical
# baseline moves with each live run; its numbers are not what these tests pin).
BASELINE = REPO / "benchmarks" / "runtime" / "2026-09-18-session-a.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replayer = _load("replay_compiler")
probe = _load("latency_probe")


def prefixed(*args, **kwargs) -> str:
    """A stand-in second compiler for the diff test."""
    return "PROSE. " + compile_clip(*args, **kwargs)


# ── The mock run ─────────────────────────────────────────────────────

class Scripted(Player):
    """Plays instantly and speaks a given line when a given clip starts."""

    def __init__(self, lines: dict[int, str]) -> None:
        super().__init__(time_scale=1000)
        self.lines = dict(lines)

    def prepare(self, item) -> None:
        pass

    async def play(self, item) -> None:
        if item.turn in self.lines:
            self.push_input(self.lines.pop(item.turn))
        await self._hold(item.duration)


def _config(**over) -> Config:
    base = dict(name="replaytest", graph="director", llm_backend="mock", model="mock",
                compaction_interval=4, showrunner_min_gap=1, buffer_seconds=0.0)
    base.update(over)
    return Config(**base)


async def _mock_run(log_dir: str, *, segments: int = 8) -> tuple[Path, Path, dict[int, dict], Config]:
    """Runs the live loop on the mock backend, capturing every BeatPlan the
    Director returned by the segment it was asked for (the last one at a
    segment wins, as a re-ask replaces the plan before it)."""
    captured: dict[int, dict] = {}
    original = MockBackend.generate_structured

    async def capture(self, messages, schema, *args, **kwargs):
        text, usage = await original(self, messages, schema, *args, **kwargs)
        if schema.__name__ == "BeatPlan":
            captured[_segment_number(messages)] = json.loads(text)
        return text, usage

    config = _config()
    with mock.patch.object(MockBackend, "generate_structured", capture):
        await run_live(config=config, story=Story.from_json(STORY), log_dir=log_dir,
                       player=Scripted({3: "A soccer ball rolls in and stops at his feet."}), max_segments=segments)
    session_dir = next((Path(log_dir) / "sessions").iterdir())
    return session_dir, Path(log_dir) / f"{session_dir.name}.md", captured, config


def test_replay_reproduces_the_engine_prompts():
    with tempfile.TemporaryDirectory() as d:
        session_dir, transcript, plans, config = asyncio.run(_mock_run(d))
        events = replayer.read_events(session_dir)
        assert len(events) >= 6 and any(e["user_input"] for e in events)
        story = Story.from_json(STORY)

        data = replayer.build_replay(events, plans, story=str(STORY), session=session_dir.name)
        assert data["schema"] == replayer.REPLAY_SCHEMA and data["missing_beats"] == []
        assert len(data["beats"]) == len({e["beat"] for e in events})
        assert [c["segment"] for c in data["clips"]] == [e["segment"] for e in events]

        rows = replayer.replay(data, compiler=replayer.load_compiler(replayer.DEFAULT_COMPILER), config=config, story=story)
        expected = replayer.transcript_prompts(transcript)
        assert len(expected) == len(events) and all("prompt" in r for r in rows)
        assert replayer.verify(rows, expected) == []
        assert rows[0]["opening"] and not rows[1]["opening"]
        assert rows[0]["words"] > rows[1]["words"]                       # the opening carries the full style and look
        assert replayer.word_stats(rows)["skipped"] == 0

        # A beat without a plan is reported, never guessed.
        broken = json.loads(json.dumps(data))
        del broken["beats"]["1"]
        rows2 = replayer.replay(broken, compiler=compile_clip, config=config, story=story)
        assert rows2[0]["note"] == "no plan for this beat" and "prompt" not in rows2[0]
        assert replayer.word_stats(rows2)["skipped"] == sum(1 for c in data["clips"] if c["beat"] == 1)
        assert replayer.verify(rows2, expected)[0]["segment"] == 1

        # The command line: a chain, a verify against the transcript, a diff against another compiler.
        replay_path, cfg_path = Path(d) / "replay.json", Path(d) / "config.yaml"
        replay_path.write_text(json.dumps(data))
        cfg_path.write_text(yaml.safe_dump(json.loads(config.model_dump_json())))
        out = Path(d) / "prompts.json"
        rc = replayer.main(["run", str(replay_path), "--config", str(cfg_path), "--out", str(out),
                            "--verify", str(transcript), "--first", "2", "--count", "3"])
        assert rc == 0
        result = json.loads(out.read_text())
        assert [c["segment"] for c in result["clips"]] == [2, 3, 4] and result["verify"]["mismatches"] == []
        assert result["compiler"] == replayer.DEFAULT_COMPILER and result["stats"]["compiled"] == 3

        out2 = Path(d) / "prose.json"
        rc = replayer.main(["run", str(replay_path), "--config", str(cfg_path), "--out", str(out2),
                            "--compiler", "tests.test_replay:prefixed", "--diff", str(out), "--first", "2", "--count", "3"])
        assert rc == 0
        diff = json.loads(out2.read_text())["diff"]
        assert diff["compared"] == 3 and diff["identical"] == 0
        assert diff["words_mean"] == diff["other_words_mean"] + 1 and len(diff["diffs"]) == 3
        assert "PROSE." in diff["diffs"][0]["diff"]

        # A verify that fails exits 1.
        assert replayer.main(["run", str(replay_path), "--config", str(cfg_path), "--compiler", "tests.test_replay:prefixed",
                              "--verify", str(transcript)]) == 1


def test_plans_from_a_langfuse_export():
    plan = {"intent": "first", "framing": "medium shot", "clips": [{"action": "a", "end_state": "b"}]}
    retry = {**plan, "intent": "second try"}
    export = {"observations": [
        {"name": "director", "type": "GENERATION", "startTime": "2026-09-18T14:56:00Z", "metadata": {"turn": 1},
         "output": {"role": "assistant", "content": json.dumps(plan)}},
        {"name": "director", "type": "GENERATION", "startTime": "2026-09-18T14:56:05Z", "metadata": {"turn": 1},
         "output": {"role": "assistant", "content": json.dumps(retry)}},
        {"name": "showrunner", "type": "GENERATION", "startTime": "2026-09-18T14:56:06Z", "metadata": {"turn": 1},
         "output": {"role": "assistant", "content": "{}"}},
        {"name": "director", "type": "GENERATION", "startTime": "2026-09-18T14:57:00Z", "metadata": {"turn": 4},
         "output": {"role": "assistant", "content": "not json"}},
        {"name": "director", "type": "GENERATION", "startTime": "2026-09-18T14:58:00Z", "metadata": {"turn": 6},
         "output": {"role": "assistant", "content": json.dumps({**plan, "intent": "third"})}},
    ]}
    plans = replayer.plans_from_langfuse(export)
    assert sorted(plans) == [1, 6] and plans[1]["intent"] == "second try" and plans[6]["intent"] == "third"
    events = [{"segment": 1, "beat": 1, "clip_index": 1, "clip_count": 1, "voiceover": "", "beat_intent": "second try"},
              {"segment": 2, "beat": 2, "clip_index": 1, "clip_count": 1, "voiceover": "x", "beat_intent": "gone"},
              {"segment": 3, "beat": 2, "clip_index": 2, "clip_count": 2, "voiceover": ""}]
    data = replayer.build_replay(events, plans, story="stories/lego-void", session="s")
    assert list(data["beats"]) == ["1"] and data["missing_beats"] == [{"beat": 2, "segment": 2, "intent": "gone"}]
    assert [c["voiceover_kept"] for c in data["clips"]] == [False, True, True]


# ── The warm probe ───────────────────────────────────────────────────

def _rows(ratios: list[float], *, clip_s: float = 6.0417, walls: list[float] | None = None) -> list[dict]:
    return [{"index": i, "ratio": r, "wall_s": (walls[i] if walls else r * clip_s), "clip_s": clip_s,
             "stages": {"transformer_total": 3.0, "forwards": 8}} for i, r in enumerate(ratios)]


def test_compare_warm_against_the_baseline():
    baseline = json.loads(BASELINE.read_text())
    warm_after = baseline["cold_warm"]["warm_after_clips"]
    ok = probe.compare_warm(_rows([1.5] * warm_after + [0.79, 0.80, 0.78, 0.81, 0.79]), baseline)
    assert ok["warm"] == 5 and all(c["result"] == "PASS" for c in ok["checks"])
    assert ok["stages_mean_s"] == {"forwards": 8.0, "transformer_total": 3.0}
    slow = probe.compare_warm(_rows([1.5] * warm_after + [0.85] * 5), baseline)
    results = {c["metric"]: c["result"] for c in slow["checks"]}
    assert results["ratio_p50"] == "FAIL" and results["ratio_p95"] == "FAIL" and results["mid_run_compiles"] == "PASS"
    tail = probe.compare_warm(_rows([1.5] * warm_after + [0.79, 0.79, 0.79, 0.79, 0.90]), baseline)
    results = {c["metric"]: c["result"] for c in tail["checks"]}
    assert results["ratio_p50"] == "PASS" and results["ratio_max"] == "FAIL"
    compiled = probe.compare_warm(_rows([1.5] * warm_after + [0.79, 0.79, 3.0, 0.79, 0.79]), baseline)
    check = next(c for c in compiled["checks"] if c["metric"] == "mid_run_compiles")
    assert check["result"] == "FAIL" and check["at"] == [warm_after + 2]
    short = probe.compare_warm(_rows([1.5] * warm_after), baseline)
    assert short["warm"] == 0 and all(c["result"] == "FAIL" and c.get("note") for c in short["checks"])
    assert probe.parse_timings("text=0.10;transformer_total=3.20;forwards=8;bad") == {"text": 0.1, "transformer_total": 3.2, "forwards": 8.0}


def _tiny_clip(path: Path, seconds: float = 0.2) -> bytes:
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", f"color=c=white:s=128x80:d={seconds}:r=24",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


async def _fake_server(clip: bytes, delays, *, clip_s: float = 0.2) -> tuple[web.AppRunner, str, dict]:
    """An LTX-2.5 server stand-in: /health, /reset and /generate with the
    real response headers, each render taking its `delays` entry."""
    calls: dict = {"resets": 0, "payloads": []}

    async def health(_req):
        return web.json_response({"status": "ready", "width": 640, "height": 352, "frame_rate": 24, "variant": "distilled",
                                  "steps": 8, "gpu": "fake", "attention": "sdpa", "quant": "fp8", "compiled": True,
                                  "encoder": "libx264"})

    async def reset(_req):
        calls["resets"] += 1
        return web.json_response({"status": "reset"})

    async def generate(req):
        body = await req.json()
        i = len(calls["payloads"])
        calls["payloads"].append(body)
        d = delays[i] if isinstance(delays, list) else delays
        await asyncio.sleep(d)
        return web.Response(body=clip, content_type="video/mp4", headers={
            "X-Duration-S": f"{clip_s:.3f}", "X-Latency-S": f"{d * 0.8:.3f}", "X-Encode-S": f"{d * 0.2:.3f}",
            "X-Num-Frames": str(body["num_frames"]),
            "X-Timings": f"text=0.01;transformer_total={d * 0.7:.3f};vae_decode={d * 0.1:.3f};forwards=8"})

    app = web.Application()
    app.add_routes([web.get("/health", health), web.post("/reset", reset), web.post("/generate", generate)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}", calls


async def test_probe_warm_mode_against_a_fake_server():
    with tempfile.TemporaryDirectory() as d:
        clip = _tiny_clip(Path(d) / "clip.mp4")
        prompts = Path(d) / "prompts.json"
        prompts.write_text(json.dumps({"schema": "prompts/1", "clips": [
            {"segment": 1, "opening": True, "prompt": "the opening prompt"}, {"segment": 2, "prompt": "the second"}]}))
        common = ["--out", d, "--baseline", str(BASELINE), "--story", str(STORY)]

        runner, url, calls = await _fake_server(clip, 0.12)               # 0.6× of a 0.2 s clip
        try:
            rc = await asyncio.to_thread(probe.main, ["--server", url, "--label", "pass", "--warm", "6",
                                                      "--prompts", str(prompts), *common])
        finally:
            await runner.cleanup()
        assert rc == 0
        result = json.loads((Path(d) / "pass" / "probe.json").read_text())
        assert result["mode"] == "warm" and len(result["rows"]) == 6 and result["comparison"]["warm"] == 3
        assert all(c["result"] == "PASS" for c in result["comparison"]["checks"])
        assert result["comparison"]["stages_mean_s"]["forwards"] == 8.0 and result["frames"] == 145
        assert calls["resets"] == 1
        first, second = calls["payloads"][0], calls["payloads"][1]
        assert first["reset_window"] and first["reference_image"] and first["num_frames"] == 145
        assert not second["reset_window"] and second["reference_image"] is None
        assert [p["prompt"] for p in calls["payloads"]][:3] == ["the opening prompt", "the second", "the opening prompt"]
        assert [p["seed"] for p in calls["payloads"]] == [1234 + i for i in range(6)]
        assert (Path(d) / "pass" / "warm_00_cut.mp4").read_bytes() == clip
        assert result["rows"][0]["cut"] and not result["rows"][0]["warm"] and result["rows"][3]["warm"]

        runner, url, calls = await _fake_server(clip, 0.2)                 # 1.0×: over the baseline
        try:
            rc = await asyncio.to_thread(probe.main, ["--server", url, "--label", "slow", "--warm", "5", *common])
        finally:
            await runner.cleanup()
        assert rc == 1
        checks = {c["metric"]: c["result"] for c in json.loads((Path(d) / "slow" / "probe.json").read_text())["comparison"]["checks"]}
        assert checks["ratio_p50"] == "FAIL" and checks["mid_run_compiles"] == "PASS"
        assert "soccer ball" in calls["payloads"][0]["prompt"]                # the built-in pair

        runner, url, calls = await _fake_server(clip, [0.12, 0.12, 0.12, 0.12, 0.6, 0.12])   # a compile mid-run
        try:
            rc = await asyncio.to_thread(probe.main, ["--server", url, "--label", "compile", "--warm", "6", *common])
        finally:
            await runner.cleanup()
        assert rc == 1
        check = next(c for c in json.loads((Path(d) / "compile" / "probe.json").read_text())["comparison"]["checks"]
                     if c["metric"] == "mid_run_compiles")
        assert check["result"] == "FAIL" and check["at"] == [4]

        runner, url, calls = await _fake_server(clip, 0.05)                # the sweep mode still works
        try:
            rc = await asyncio.to_thread(probe.main, ["--server", url, "--label", "sweep", "--seconds", "1", "--repeat", "2", *common])
        finally:
            await runner.cleanup()
        assert rc == 0
        result = json.loads((Path(d) / "sweep" / "probe.json").read_text())
        assert result["mode"] == "sweep" and [r["seconds"] for r in result["rows"]] == [1, 1]
        assert calls["payloads"][0]["num_frames"] == 25 and calls["payloads"][0]["reset_window"]
