"""Scenario runner — drives a config through a list of user commands.

Used both for benchmark runs (no UI, bypass buffer) and for `play
--scenario` which can optionally reuse the same loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from src.graph import build_graph
from src.i2v import build_i2v_backend, extract_last_frame
from src.i2v.base import I2VBackend
from src.llm import build_backend
from src.models.config import Config
from src.models.story import Story
from src.playback import (
    concat_videos_and_mux_audio,
    mux_audio_into_video,
)
from src.state.story_state import HistoryEntry, StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.ui.terminal import TerminalUI
from src.util.interaction_logger import InteractionLogger
from src.util.media import probe_duration
from src.util.story_log import StoryLogger

logger = logging.getLogger(__name__)


def load_scenario(path: Path | str) -> list[str]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def run_scenario(
    *,
    config: Config,
    story: Story,
    scenario_path: Path | str,
    log_dir: Path | str = "logs",
) -> StoryState:
    """Run a pre-defined scenario synchronously, turn by turn.

    Bypasses any pipeline buffer — each turn runs the graph to completion
    before moving on. Returns the final `StoryState` for inspection.
    """
    turns = load_scenario(scenario_path)

    state = StoryState.initialize(story, config_name=config.name)

    interaction_logger = InteractionLogger(
        session_label=f"{config.name}_{Path(scenario_path).stem}",
        config_name=config.name,
        scenario=Path(scenario_path).name,
        story_title=story.title,
        log_dir=log_dir,
    )
    story_logger = StoryLogger(interaction_logger)

    llm = build_backend(config.llm_backend, config.model)
    tts = _maybe_tts(config, log_dir)
    i2v = _maybe_i2v(config, log_dir)

    graph = build_graph(
        config.graph,
        llm=llm,
        config=config,
        interaction_logger=interaction_logger,
        tts=tts,
    )

    seed_image: str = config.i2v_seed_image
    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    for turn_number, user_input in enumerate(turns, start=1):
        state.turn_number = turn_number
        state.user_input = user_input

        interaction_logger.log_event("turn_start", turn_number, {"user_input": user_input})

        result = await graph.ainvoke(state)
        state = _coerce_state(result)

        if i2v is not None and state.current_shot is not None:
            seed_image, _silent, _playable = await _render_turn(
                state=state,
                i2v=i2v,
                seed_image=seed_image,
                frames_dir=frames_dir,
                video_dir=video_dir,
                interaction_logger=interaction_logger,
            )

        _commit_history(state)
        _log_story_turn(story_logger, state)

    interaction_logger.log_event(
        "session_end",
        state.turn_number,
        {"turns_completed": len(turns)},
    )
    return state


def _maybe_tts(config: Config, log_dir: Path | str) -> ElevenLabsTTS | None:
    if not config.audio_enabled:
        return None
    audio_dir = Path(log_dir) / "audio"
    return ElevenLabsTTS(
        voice_id=config.elevenlabs_voice_id,
        audio_dir=audio_dir,
    )


def _maybe_i2v(config: Config, log_dir: Path | str) -> I2VBackend | None:
    if not config.video_enabled:
        return None
    video_dir = Path(log_dir) / "video"
    return build_i2v_backend(
        config.i2v_backend,
        model=config.i2v_model,
        resolution=config.i2v_resolution,
        duration=config.i2v_duration,
        output_dir=video_dir,
        audio=config.i2v_audio,
    )


async def _render_turn(
    *,
    state: StoryState,
    i2v: I2VBackend,
    seed_image: str,
    frames_dir: Path,
    video_dir: Path,
    interaction_logger: InteractionLogger,
    mux_inline: bool = True,
) -> tuple[str, str | None, str | None]:
    """Render one clip, threading the seed image forward.

    Returns `(next_seed_image, silent_video_path, playable_path)` where:
    - `next_seed_image` always set (falls back to current seed on failure)
    - `silent_video_path` is the raw DashScope mp4 (or None if render failed)
    - `playable_path` is the muxed mp4 when `mux_inline=True` AND audio
      was produced this turn; otherwise the silent path; or None on
      complete render failure.

    `mux_inline=False` is for the live loop, which handles muxing
    itself so it can defer single-audio-over-multiple-clips spans.
    """
    if not Path(seed_image).exists():
        logger.warning("Seed image missing for turn %s: %s — skipping render",
                       state.turn_number, seed_image)
        interaction_logger.log_event("i2v_skip", state.turn_number,
                                     {"reason": "missing_seed", "seed_image": seed_image})
        return seed_image, None, None

    prompt = state.current_shot.i2v_prompt
    video_path = await i2v.synthesize(
        image_path=seed_image,
        prompt=prompt,
        turn=state.turn_number,
        duration=state.current_shot.duration_seconds,
    )
    if not video_path:
        interaction_logger.log_event("i2v_render_failed", state.turn_number,
                                     {"seed_image": seed_image})
        return seed_image, None, None

    next_seed = frames_dir / f"turn_{state.turn_number:04d}_last.png"
    extracted = await asyncio.to_thread(extract_last_frame, video_path, next_seed)

    playable_path: str | None = video_path
    muxed_path: str | None = None
    if mux_inline and state.current_audio_path:
        out = video_dir / f"turn_{state.turn_number:04d}_muxed.mp4"
        muxed_path = await mux_audio_into_video(
            video_path=video_path,
            audio_path=state.current_audio_path,
            output_path=out,
        )
        if muxed_path:
            playable_path = muxed_path

    interaction_logger.log_event("i2v_render", state.turn_number, {
        "seed_image": seed_image,
        "video_path": video_path,
        "audio_path": state.current_audio_path or None,
        "muxed_path": muxed_path,
        "playable_path": playable_path,
        "next_seed_image": extracted or seed_image,
    })
    return extracted or seed_image, video_path, playable_path


def _coerce_state(result) -> StoryState:
    """LangGraph may hand us either a Pydantic StoryState or a dict."""
    if isinstance(result, StoryState):
        return result
    if isinstance(result, dict):
        return StoryState(**result)
    raise TypeError(f"Unexpected graph output type: {type(result)!r}")


def _commit_history(state: StoryState) -> None:
    """Append the committed turn to history, if all three outputs exist."""
    if (
        state.current_beat is None
        or state.current_shot is None
        or state.current_commentary is None
    ):
        logger.warning(
            "Turn %s skipped history commit — missing one of beat/shot/commentary",
            state.turn_number,
        )
        return

    state.history.append(HistoryEntry(
        turn=state.turn_number,
        user_input=state.user_input,
        beat=state.current_beat,
        shot=state.current_shot,
        commentary=state.current_commentary,
    ))


def _log_story_turn(story_logger: StoryLogger, state: StoryState) -> None:
    story_logger.log_turn(
        turn=state.turn_number,
        user_input=state.user_input,
        beat=state.current_beat,
        shot=state.current_shot,
        commentary=state.current_commentary,
        world_state=state.world_state,
        narrative_memory=state.narrative_memory,
        context_brief=state.context_brief,
    )


async def _launch_persistent_ffplay(
    clip_path: str,
) -> asyncio.subprocess.Process | None:
    """Launch ffplay *without* ``-autoexit`` so it holds the last frame."""
    if not shutil.which("ffplay"):
        return None
    try:
        return await asyncio.create_subprocess_exec(
            "ffplay",
            "-noborder",
            "-loglevel", "quiet",
            "-window_title", "ClankerStudios",
            clip_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("Could not launch ffplay for %s", clip_path)
        return None


async def _get_clip_duration(clip_path: str, fallback: float = 5.0) -> float:
    dur = await probe_duration(clip_path)
    return dur if dur is not None else fallback


async def _save_full_session(
    clips: list[str], output_path: Path,
) -> str | None:
    """Concat all played clips into one video via ffmpeg concat demuxer."""
    if not clips or not shutil.which("ffmpeg"):
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filelist = output_path.with_suffix(".txt")
    filelist.write_text(
        "\n".join(f"file '{Path(c).resolve()}'" for c in clips),
        encoding="utf-8",
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(filelist), "-c", "copy", str(output_path),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        code = await proc.wait()
        filelist.unlink(missing_ok=True)
        if code == 0:
            return str(output_path)
        logger.warning("ffmpeg concat exited %s", code)
        return None
    except Exception:
        logger.exception("Failed to save full session video")
        return None


async def run_play(
    *,
    config: Config,
    story: Story,
    log_dir: Path | str = "logs",
    ui: TerminalUI | None = None,
) -> StoryState:
    """Interactive loop — prompts user between turns. Ctrl+C to quit."""
    state = StoryState.initialize(story, config_name=config.name)
    interaction_logger = InteractionLogger(
        session_label=f"{config.name}_play",
        config_name=config.name,
        scenario="interactive",
        story_title=story.title,
        log_dir=log_dir,
    )
    story_logger = StoryLogger(interaction_logger)

    llm = build_backend(config.llm_backend, config.model)
    tts = _maybe_tts(config, log_dir)
    i2v = _maybe_i2v(config, log_dir)

    graph = build_graph(
        config.graph,
        llm=llm,
        config=config,
        interaction_logger=interaction_logger,
        tts=tts,
    )

    ui = ui or TerminalUI()

    seed_image: str = config.i2v_seed_image
    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    turn_number = 0
    try:
        while True:
            turn_number += 1
            user_input = await ui.prompt_for_input()
            state.turn_number = turn_number
            state.user_input = user_input

            interaction_logger.log_event("turn_start", turn_number, {"user_input": user_input})

            result = await graph.ainvoke(state)
            state = _coerce_state(result)

            if i2v is not None and state.current_shot is not None:
                seed_image, _playable_path = await _render_turn(
                    state=state,
                    i2v=i2v,
                    seed_image=seed_image,
                    frames_dir=frames_dir,
                    video_dir=video_dir,
                    interaction_logger=interaction_logger,
                )

            _commit_history(state)
            _log_story_turn(story_logger, state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n(quit)")
    finally:
        interaction_logger.log_event(
            "session_end",
            state.turn_number,
            {"turns_completed": state.turn_number},
        )
    return state


async def run_live(
    *,
    config: Config,
    story: Story,
    log_dir: Path | str = "logs",
) -> StoryState:
    """Live demo loop — concurrent producer + player + stdin reader.

    The producer keeps generating turns and pushing playable clips
    onto a bounded queue (size = config.video_buffer_clips). The
    player pulls clips and pops a video window via ffplay. A
    background stdin thread reads user input and posts it to a
    queue; the producer drains that queue at the start of each
    turn and applies whatever's there as `user_input`. If the queue
    is empty the story advances on `short_term_narrative` — silent
    turns are normal.

    Pre-buffer behavior: the player blocks on the first clip's
    `queue.get()`, so playback only starts once the first render
    completes (~30s with DashScope).
    """
    state = StoryState.initialize(story, config_name=config.name)
    interaction_logger = InteractionLogger(
        session_label=f"{config.name}_live",
        config_name=config.name,
        scenario="live",
        story_title=story.title,
        log_dir=log_dir,
    )
    story_logger = StoryLogger(interaction_logger)

    llm = build_backend(config.llm_backend, config.model)
    tts = _maybe_tts(config, log_dir)
    i2v = _maybe_i2v(config, log_dir)
    if i2v is None:
        raise RuntimeError(
            "run_live requires video_enabled: true in the config; otherwise use run_play."
        )

    graph = build_graph(
        config.graph, llm=llm, config=config,
        interaction_logger=interaction_logger, tts=tts,
    )

    print(
        f"Pre-buffering up to {config.video_buffer_clips} clip(s); "
        f"first render takes ~30s on DashScope. Type to steer; Ctrl+C to quit.",
        flush=True,
    )

    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    loop = asyncio.get_running_loop()
    user_input_queue: asyncio.Queue[str] = asyncio.Queue()
    clip_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, config.video_buffer_clips))

    stop_event = asyncio.Event()
    played_clips: list[str] = []

    def _stdin_loop() -> None:
        """Blocking stdin reader running on a daemon thread.

        Re-emits a `› ` prompt after every Enter so the terminal feels
        like a chat input field — the previous line stays as scrollback,
        a fresh prompt waits below.
        """
        sys.stdout.write("› ")
        sys.stdout.flush()
        while not stop_event.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:  # EOF
                return
            line = line.strip()
            asyncio.run_coroutine_threadsafe(user_input_queue.put(line), loop)
            sys.stdout.write("› ")
            sys.stdout.flush()

    threading.Thread(target=_stdin_loop, daemon=True).start()

    # The producer mutates these state fields directly so the next turn's
    # Attenborough call sees up-to-date pacing. Mark the loop as managing
    # pacing and open the gate for turn 1 by setting silence at the floor.
    state.pacing_managed = True
    state.silence_seconds = config.min_pause_seconds
    state.audio_seconds_owed = 0.0

    async def producer() -> None:
        nonlocal state
        seed_image = config.i2v_seed_image
        turn = 0
        # Span-pending: collects silent clips behind a voiceover whose audio
        # spills past one clip. Closed (concat+mux+enqueue) once the
        # accumulated clip duration covers the audio.
        # Shape: {"clips": [paths], "audio": str, "owed": float,
        #         "audio_dur": float, "start_turn": int}
        pending_span: dict | None = None
        try:
            while not stop_event.is_set():
                turn += 1
                # Drain pending input — keep the most recent non-empty entry.
                user_input = ""
                while not user_input_queue.empty():
                    candidate = await user_input_queue.get()
                    if candidate:
                        user_input = candidate

                state.turn_number = turn
                state.user_input = user_input
                interaction_logger.log_event(
                    "turn_start", turn, {"user_input": user_input},
                )

                result = await graph.ainvoke(state)
                state = _coerce_state(result)
                # graph.ainvoke rebuilt state from a dict — re-arm the
                # pacing flag so the next turn's gating still applies.
                state.pacing_managed = True

                silent: str | None = None
                if state.current_shot is not None:
                    seed_image, silent, _ = await _render_turn(
                        state=state,
                        i2v=i2v,
                        seed_image=seed_image,
                        frames_dir=frames_dir,
                        video_dir=video_dir,
                        interaction_logger=interaction_logger,
                        mux_inline=False,  # live loop owns muxing
                    )

                _commit_history(state)
                _log_story_turn(story_logger, state)

                if silent is None:
                    # Render failed or skipped — nothing to enqueue. Abort any
                    # pending span (the audio would no longer line up with the
                    # visual continuity anyway) and reset owed so the next
                    # turn's Attenborough is not artificially held.
                    if pending_span is not None:
                        interaction_logger.log_event(
                            "playback_span_abort", turn,
                            {"start_turn": pending_span["start_turn"],
                             "reason": "render_failed_mid_span"},
                        )
                        pending_span = None
                    state.audio_seconds_owed = 0.0
                    interaction_logger.log_event(
                        "live_no_playable", turn,
                        {"reason": "render_failed_or_skipped"},
                    )
                    continue

                clip_dur = await _get_clip_duration(silent, fallback=float(config.i2v_duration))
                state.last_clip_duration = clip_dur

                # Mid-span: this turn's Attenborough was held silent because
                # the previous voiceover still owes audio. Add the silent
                # clip to the span and pay it down.
                if pending_span is not None:
                    pending_span["clips"].append(silent)
                    pending_span["owed"] -= clip_dur
                    state.audio_seconds_owed = max(0.0, pending_span["owed"])
                    if pending_span["owed"] <= 0.001:
                        out = video_dir / f"turn_{pending_span['start_turn']:04d}_span.mp4"
                        combined = await concat_videos_and_mux_audio(
                            video_paths=pending_span["clips"],
                            audio_path=pending_span["audio"],
                            output_path=out,
                        )
                        chosen = combined or pending_span["clips"][0]
                        interaction_logger.log_event(
                            "playback_span_complete", turn, {
                                "start_turn": pending_span["start_turn"],
                                "clip_count": len(pending_span["clips"]),
                                "audio_dur": pending_span["audio_dur"],
                                "video_paths": pending_span["clips"],
                                "audio_path": pending_span["audio"],
                                "playable": chosen,
                                "concat_succeeded": combined is not None,
                            },
                        )
                        pending_span = None
                        # Span finished: reset silence to start counting again.
                        state.silence_seconds = 0.0
                        await clip_queue.put(chosen)
                    continue

                audio = state.current_audio_path

                if audio:
                    audio_dur = await probe_duration(audio)
                    if audio_dur is None:
                        # Probe failed — assume audio fits in one clip and
                        # let the single-clip mux clamp via -shortest.
                        audio_dur = clip_dur
                    if audio_dur > clip_dur + 0.05:
                        # Voiceover spills past this clip — open a span.
                        owed = audio_dur - clip_dur
                        pending_span = {
                            "clips": [silent],
                            "audio": audio,
                            "owed": owed,
                            "audio_dur": audio_dur,
                            "start_turn": turn,
                        }
                        state.audio_seconds_owed = owed
                        state.silence_seconds = 0.0
                        interaction_logger.log_event(
                            "playback_span_open", turn,
                            {"audio_dur": audio_dur, "clip_dur": clip_dur,
                             "owed": owed, "audio_path": audio},
                        )
                        continue

                    # Audio fits in one clip — mux and enqueue.
                    out = video_dir / f"turn_{turn:04d}_muxed.mp4"
                    muxed = await mux_audio_into_video(
                        video_path=silent,
                        audio_path=audio,
                        output_path=out,
                    )
                    state.audio_seconds_owed = 0.0
                    state.silence_seconds = 0.0
                    await clip_queue.put(muxed or silent)
                else:
                    # Pure silence — accumulate the floor.
                    state.audio_seconds_owed = 0.0
                    state.silence_seconds += clip_dur
                    await clip_queue.put(silent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live producer crashed on turn %s", turn)
            stop_event.set()

    async def consumer() -> None:
        gate = max(1, config.prebuffer_clips)
        try:
            # Pre-buffer gate: wait until N clips are queued.
            while clip_queue.qsize() < gate and not stop_event.is_set():
                await asyncio.sleep(0.5)
            if stop_event.is_set():
                return
            print(
                f"Pre-buffer ready ({clip_queue.qsize()} clips). Starting playback.",
                flush=True,
            )

            current_proc: asyncio.subprocess.Process | None = None

            while not stop_event.is_set():
                try:
                    clip_path = clip_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # Queue empty — current ffplay holds last frame while we
                    # wait for the producer. The `› ` prompt is enough cue.
                    clip_path = await clip_queue.get()

                # Launch new window BEFORE killing old → no flicker.
                new_proc = await _launch_persistent_ffplay(clip_path)
                # Give the new window time to render its first frame
                # before tearing down the old one.
                await asyncio.sleep(0.5)

                if current_proc is not None and current_proc.returncode is None:
                    try:
                        current_proc.kill()
                        await current_proc.wait()
                    except Exception:
                        pass

                current_proc = new_proc
                played_clips.append(clip_path)
                interaction_logger.log_event(
                    "playback_start", state.turn_number,
                    {"clip_path": clip_path},
                )

                duration = await _get_clip_duration(clip_path)
                await asyncio.sleep(duration)

                interaction_logger.log_event(
                    "playback_end", state.turn_number,
                    {"clip_path": clip_path},
                )
                # After sleep, ffplay is showing frozen last frame.
                # Loop back — if next clip is ready, swap immediately.
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live consumer crashed")
            stop_event.set()
        finally:
            if current_proc is not None and current_proc.returncode is None:
                try:
                    current_proc.kill()
                except Exception:
                    pass

    producer_task = asyncio.create_task(producer())
    consumer_task = asyncio.create_task(consumer())

    try:
        await asyncio.gather(producer_task, consumer_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n(quit)")
    finally:
        stop_event.set()
        for task in (producer_task, consumer_task):
            if not task.done():
                task.cancel()
        for task in (producer_task, consumer_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        interaction_logger.log_event(
            "session_end", state.turn_number,
            {"turns_completed": state.turn_number},
        )

        if played_clips:
            out = video_dir / "full_session.mp4"
            print("Saving full session video…", flush=True)
            saved = await _save_full_session(played_clips, out)
            if saved:
                print(f"Saved: {saved}", flush=True)
            else:
                print("Could not save session video.", flush=True)
    return state
