"""The run loops.

`run_live` is the film: a producer generating segments and a `Player`
(the web player or an ffplay window) showing them while the viewer steers
mid-stream. `run_play` prompts in the terminal between segments, for
headless development without a player.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from src.graph import build_graph
from src.i2v import build_i2v_backend
from src.i2v.base import I2VBackend
from src.llm import build_backend
from src.models.config import Config
from src.models.story import Story
from src.playback import (
    Playable,
    Player,
    TerminalPlayer,
    concat_videos_and_mux_audio,
    mux_audio_into_video,
)
from src.state.story_state import HistoryEntry, StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.ui.terminal import TerminalUI
from src.util.langfuse_setup import event as langfuse_event
from src.util.langfuse_setup import flush as langfuse_flush
from src.util.langfuse_setup import set_trace_output, turn_span
from src.util.media import probe_duration
from src.util.story_log import StoryLogger

# Late start under pipelining: the Director estimate is scaled by
# LATE_START_SAFETY and LATE_START_MARGIN_S is kept in hand, so a slower
# than usual Director call does not leave the renderer idle.
LATE_START_SAFETY = 1.25
LATE_START_MARGIN_S = 0.5

logger = logging.getLogger(__name__)


def _make_session_id(config_name: str, label: str) -> str:
    """Stable session id used for Langfuse and the on-disk Markdown log."""
    return f"{config_name}_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _trace_output(state: StoryState) -> dict:
    """What to surface as the trace.output in the Langfuse Sessions view."""
    return {
        "narration": state.current_beat.narration if state.current_beat else None,
        "voiceover": state.current_commentary.voiceover if state.current_commentary else None,
    }


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
        server_url=config.ltxv_server_url,
        continuity_strength=config.ltxv_continuity_strength,
        continuity_frames=config.ltxv_continuity_frames,
        output_dir=video_dir,
        reference_fit=config.i2v_reference_fit,
        reference_fill=config.i2v_reference_fill,
        negative_prompt=config.ltxv_negative_prompt,
        seed=config.ltxv_seed,
        anchor_strength=config.ltxv_anchor_strength,
        anchor_index=config.ltxv_anchor_index,
        replay_dir=config.i2v_replay_dir,
        replay_delay=config.i2v_replay_delay,
    )


async def _render_turn(
    *,
    state: StoryState,
    i2v: I2VBackend,
    seed_image: str,
    frames_dir: Path,
    video_dir: Path,
    mux_inline: bool = True,
) -> tuple[str, str | None, str | None]:
    """Render one clip, threading the seed image forward.

    Returns `(next_seed_image, silent_video_path, playable_path)` where:
    - `next_seed_image` always set (falls back to current seed on failure)
    - `silent_video_path` is the raw i2v mp4 (or None if render failed)
    - `playable_path` is the muxed mp4 when `mux_inline=True` AND audio
      was produced this turn; otherwise the silent path; or None on
      complete render failure.

    `mux_inline=False` is for the live loop, which handles muxing
    itself so it can defer single-audio-over-multiple-clips spans.
    """
    if not Path(seed_image).exists():
        logger.warning("Seed image missing for turn %s: %s — skipping render",
                       state.turn_number, seed_image)
        langfuse_event(
            "i2v_skip",
            metadata={"turn": state.turn_number,
                      "reason": "missing_seed", "seed_image": seed_image},
            level="WARNING",
        )
        return seed_image, None, None

    prompt = state.current_shot.i2v_prompt
    # Pass the segment's duration to the backend.
    shot_duration = state.current_shot.duration_seconds
    video_path = await i2v.synthesize(
        image_path=seed_image,
        prompt=prompt,
        turn=state.turn_number,
        duration=shot_duration,
        reset=state.current_transition == "cut",
    )
    if not video_path:
        langfuse_event(
            "i2v_render_failed",
            metadata={"turn": state.turn_number, "seed_image": seed_image},
            level="ERROR",
        )
        return seed_image, None, None

    # The LTXV backend owns continuity server-side (sliding window in GPU
    # memory), so no client-side frame chaining is needed.
    extracted = None

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

    langfuse_event(
        "i2v_render",
        metadata={
            "turn": state.turn_number,
            "seed_image": seed_image,
            "video_path": video_path,
            "audio_path": state.current_audio_path or None,
            "muxed_path": muxed_path,
            "playable_path": playable_path,
            "next_seed_image": extracted or seed_image,
        },
    )
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
        context_brief=state.context_brief,
        extra=state.turn_notes,
    )


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
    session_id = _make_session_id(config.name, "play")
    story_logger = StoryLogger(
        session_id=session_id,
        config_name=config.name,
        scenario="interactive",
        story_title=story.title,
        log_dir=log_dir,
    )

    llm = build_backend(config.llm_backend, config.model)
    tts = _maybe_tts(config, log_dir)
    i2v = _maybe_i2v(config, log_dir)

    graph = build_graph(
        config.graph, llm=llm, config=config, tts=tts,
        session_dir=Path(log_dir) / "sessions" / session_id,
    )

    ui = ui or TerminalUI()

    seed_image: str = config.i2v_seed_image
    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    trace_tags = [config.name, "play"]

    turn_number = 0
    try:
        while True:
            turn_number += 1
            user_input = await ui.prompt_for_input()
            state.turn_number = turn_number
            state.user_input = user_input

            with turn_span(
                turn_number=turn_number,
                session_id=session_id,
                tags=trace_tags,
                user_input=user_input,
                metadata={"config": config.name, "user_input": user_input},
            ):
                result = await graph.ainvoke(state)
                state = _coerce_state(result)

                if i2v is not None and state.current_shot is not None:
                    seed_image, _silent, _playable = await _render_turn(
                        state=state,
                        i2v=i2v,
                        seed_image=seed_image,
                        frames_dir=frames_dir,
                        video_dir=video_dir,
                    )

                set_trace_output(_trace_output(state))

            _commit_history(state)
            _log_story_turn(story_logger, state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n(quit)")
    finally:
        await graph.drain(state)
        langfuse_flush()
    return state


async def run_live(
    *,
    config: Config,
    story: Story,
    log_dir: Path | str = "logs",
    player: Player | None = None,
    max_segments: int | None = None,
) -> StoryState:
    """Live loop: a producer generating segments, a player showing them.

    The producer keeps generating turns and pushes `Playable` items onto a
    bounded queue (size = `lead_clips + 1`). The consumer hands them to the
    player one at a time; the player holds each for its duration, so the
    server is the clock. Whatever the viewer typed since the previous
    segment is read from the player at the start of each turn; when
    nothing arrived the story advances on its own — silent turns are normal.

    With a video backend each item is a rendered clip. Without one it is
    the segment's text, held for the segment's duration, so a headless
    config gives a working session in the web player.

    Lead behavior: the consumer waits for `lead_clips + 1` items before
    the first play. With the default `lead_clips: 0` playback starts as
    soon as the first item is ready and one render naturally overlaps one
    playback. `max_segments` stops the producer after that many segments
    (tests, bounded demos); otherwise it runs until Ctrl+C.

    Late start: with a backend that owns continuity, the Director call for
    segment N+2 overlaps the render of N+1, which starts when clip N starts
    playing. Reading the viewer's lines at the start of that call would
    make a line typed while watching N land only at N+3. The producer
    therefore starts the call as late as the render allows (the render
    estimate minus the Director estimate, both running averages of the
    measured times) and reads the lines then, so a line typed while a clip
    plays lands two segments later, the earliest the pipeline permits,
    without delaying any render.
    """
    state = StoryState.initialize(story, config_name=config.name)
    session_id = _make_session_id(config.name, "live")
    story_logger = StoryLogger(
        session_id=session_id,
        config_name=config.name,
        scenario="live",
        story_title=story.title,
        log_dir=log_dir,
    )

    llm = build_backend(config.llm_backend, config.model)
    tts = _maybe_tts(config, log_dir)
    i2v = _maybe_i2v(config, log_dir)
    player = player or TerminalPlayer()

    graph = build_graph(
        config.graph, llm=llm, config=config, tts=tts,
        session_dir=Path(log_dir) / "sessions" / session_id,
    )

    queue_size = max(1, config.lead_clips + 1)
    mode = "video" if i2v is not None else "text (no video backend)"
    if config.lead_clips > 0:
        print(f"Live, {mode}: pre-buffering {queue_size} segments before playback "
              f"(lead_clips={config.lead_clips}). Ctrl+C to quit.", flush=True)
    else:
        print(f"Live, {mode}: playback starts as soon as the first segment is ready. "
              "Ctrl+C to quit.", flush=True)

    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    queue: asyncio.Queue[Playable | None] = asyncio.Queue(maxsize=queue_size)
    stop_event = asyncio.Event()
    producer_done = asyncio.Event()
    played_clips: list[str] = []

    # The producer mutates these state fields directly so the next turn's
    # voice-over gate sees up-to-date pacing. Mark the loop as managing
    # pacing and open the gate for turn 1 by setting silence at the floor.
    state.pacing_managed = True
    state.silence_seconds = config.min_pause_seconds
    state.audio_seconds_owed = 0.0

    trace_tags = [config.name, "live"]

    # A backend that owns continuity (the LTX-2.5 server's sliding window)
    # depends only on the prompt, so render(N) can overlap agents(N+1).
    _pipeline_ok = i2v is not None and i2v.manages_continuity

    # Running averages of the measured render and Director times, for the
    # late start of the Director call (see the docstring). None until the
    # first measurement; no wait happens before both exist.
    est: dict[str, float | None] = {"render": None, "director": None}

    def _learn(key: str, seconds: float) -> None:
        prev = est[key]
        est[key] = seconds if prev is None else 0.5 * prev + 0.5 * seconds

    async def _enqueue(item: Playable) -> None:
        await queue.put(item)
        player.prepare(item)

    async def _bg_render_and_enqueue(
        item: Playable, *, prompt: str, seed_image: str, duration: int | None, reset: bool,
    ) -> None:
        """Render one clip in the background and enqueue it."""
        assert i2v is not None
        t0 = time.monotonic()
        video_path = await i2v.synthesize(
            image_path=seed_image, prompt=prompt, turn=item.turn, duration=duration, reset=reset,
        )
        _learn("render", time.monotonic() - t0)
        if video_path:
            item.path = video_path
            item.duration = await _get_clip_duration(
                video_path, fallback=float(duration or config.i2v_duration),
            )
            await _enqueue(item)
        else:
            langfuse_event(
                "i2v_render_failed",
                metadata={"turn": item.turn, "seed_image": seed_image},
                level="ERROR",
            )

    async def producer() -> None:
        nonlocal state
        seed_image = config.i2v_seed_image
        turn = 0
        # Span-pending: collects silent clips behind a voiceover whose audio
        # spills past one clip. Closed (concat+mux+enqueue) once the
        # accumulated clip duration covers the audio.
        # Shape: {"item": Playable, "clips": [paths], "audio": str,
        #         "owed": float, "audio_dur": float, "start_turn": int}
        pending_span: dict | None = None
        # Background render task for pipelining, and when it was fired.
        _prev_render: asyncio.Task | None = None
        _prev_render_started: float | None = None

        async def _drain_prev() -> None:
            nonlocal _prev_render
            if _prev_render is not None:
                try:
                    await _prev_render
                except Exception:
                    logger.warning("Pipelined render failed", exc_info=True)
                _prev_render = None

        try:
            while not stop_event.is_set():
                if max_segments is not None and turn >= max_segments:
                    break
                turn += 1
                if (
                    _prev_render is not None and not _prev_render.done()
                    and _prev_render_started is not None
                    and est["render"] is not None and est["director"] is not None
                ):
                    # Late start: the previous clip is playing and its
                    # successor is rendering. Start this turn's Director
                    # call only when the render is about to finish, so the
                    # viewer's lines from watching the clip are read first.
                    remaining = est["render"] - (time.monotonic() - _prev_render_started)
                    slack = remaining - est["director"] * LATE_START_SAFETY - LATE_START_MARGIN_S
                    if slack > 0:
                        player.status(f"listening for your input before segment {turn} is written")
                        try:
                            await asyncio.wait_for(asyncio.shield(_prev_render), timeout=slack)
                        except asyncio.TimeoutError:
                            pass  # the render is nearly done: start the Director now
                        except Exception:
                            pass  # _drain_prev reports a failed render
                user_input = await player.next_input()

                state.turn_number = turn
                state.user_input = user_input
                player.status(f"the Director is writing segment {turn}")

                with turn_span(
                    turn_number=turn,
                    session_id=session_id,
                    tags=trace_tags,
                    user_input=user_input,
                    metadata={"config": config.name, "user_input": user_input},
                ):
                    t_director = time.monotonic()
                    result = await graph.ainvoke(state)
                    _learn("director", time.monotonic() - t_director)
                    state = _coerce_state(result)

                    silent: str | None = None
                    handled = False
                    item: Playable | None = None
                    if state.current_shot is not None:
                        item = _playable(state)
                        if i2v is None:
                            # ── Text-only film ──────────────────────────
                            # No renderer: the segment plays as text for
                            # its own duration. Pacing follows that clock.
                            handled = True
                            _pace_expected(state, item.duration)
                            await _enqueue(item)
                        elif (
                            _pipeline_ok
                            and not state.current_audio_path
                            and pending_span is None
                        ):
                            # ── Pipelined path ──────────────────────────
                            # Wait for the previous render (the sliding
                            # window is sequential), then fire this one in
                            # the background — it overlaps with the *next*
                            # turn's agent calls.
                            handled = True
                            await _drain_prev()
                            player.status(f"rendering segment {turn}")
                            shot_dur = state.current_shot.duration_seconds
                            _prev_render_started = time.monotonic()
                            _prev_render = asyncio.create_task(
                                _bg_render_and_enqueue(
                                    item,
                                    prompt=state.current_shot.i2v_prompt,
                                    seed_image=seed_image,
                                    duration=shot_dur,
                                    reset=state.current_transition == "cut",
                                )
                            )
                            # Set pacing state now with the expected clip
                            # duration so the next voice-over decision is right.
                            _pace_expected(state, float(shot_dur))
                        else:
                            # ── Serial path (audio / span) ──
                            await _drain_prev()
                            player.status(f"rendering segment {turn}")
                            seed_image, silent, _ = await _render_turn(
                                state=state,
                                i2v=i2v,
                                seed_image=seed_image,
                                frames_dir=frames_dir,
                                video_dir=video_dir,
                                mux_inline=False,  # live loop owns muxing
                            )

                    set_trace_output(_trace_output(state))

                _commit_history(state)
                _log_story_turn(story_logger, state)

                # Text and pipelined turns already set pacing and enqueued.
                if handled:
                    continue

                if silent is None or item is None:
                    # Render failed or skipped — nothing to enqueue. Abort any
                    # pending span (the audio would no longer line up with the
                    # visual continuity anyway) and reset owed so the next
                    # turn's voice-over gate is not artificially held.
                    if pending_span is not None:
                        langfuse_event(
                            "playback_span_abort",
                            metadata={"turn": turn,
                                      "start_turn": pending_span["start_turn"],
                                      "reason": "render_failed_mid_span"},
                            level="WARNING",
                        )
                        pending_span = None
                    state.audio_seconds_owed = 0.0
                    langfuse_event(
                        "live_no_playable",
                        metadata={"turn": turn,
                                  "reason": "render_failed_or_skipped"},
                        level="WARNING",
                    )
                    continue

                clip_dur = await _get_clip_duration(silent, fallback=float(config.i2v_duration))
                state.last_clip_duration = clip_dur

                # Mid-span: this turn's voice-over was held silent because
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
                        langfuse_event(
                            "playback_span_complete",
                            metadata={
                                "turn": turn,
                                "start_turn": pending_span["start_turn"],
                                "clip_count": len(pending_span["clips"]),
                                "audio_dur": pending_span["audio_dur"],
                                "video_paths": pending_span["clips"],
                                "audio_path": pending_span["audio"],
                                "playable": chosen,
                                "concat_succeeded": combined is not None,
                            },
                        )
                        span_item: Playable = pending_span["item"]
                        span_item.path = chosen
                        span_item.duration = await _get_clip_duration(chosen, fallback=clip_dur)
                        pending_span = None
                        # Span finished: reset silence to start counting again.
                        state.silence_seconds = 0.0
                        await _enqueue(span_item)
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
                            "item": item,
                            "clips": [silent],
                            "audio": audio,
                            "owed": owed,
                            "audio_dur": audio_dur,
                            "start_turn": turn,
                        }
                        state.audio_seconds_owed = owed
                        state.silence_seconds = 0.0
                        langfuse_event(
                            "playback_span_open",
                            metadata={"turn": turn, "audio_dur": audio_dur,
                                      "clip_dur": clip_dur, "owed": owed,
                                      "audio_path": audio},
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
                    item.path = muxed or silent
                    item.duration = clip_dur
                    await _enqueue(item)
                else:
                    # Pure silence — accumulate the floor.
                    state.audio_seconds_owed = 0.0
                    state.silence_seconds += clip_dur
                    item.path = silent
                    item.duration = clip_dur
                    await _enqueue(item)
            # A bounded run: let the last render land before signalling the end.
            await _drain_prev()
        except asyncio.CancelledError:
            if _prev_render is not None:
                _prev_render.cancel()
            raise
        except Exception:
            logger.exception("Live producer crashed on turn %s", turn)
            stop_event.set()
        finally:
            producer_done.set()
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def consumer() -> None:
        gate = queue_size
        try:
            # Lead gate: wait until `lead_clips + 1` items are queued (or the
            # producer is already done). At lead=0 this is just "wait for
            # the first segment."
            while queue.qsize() < gate and not stop_event.is_set() and not producer_done.is_set():
                await asyncio.sleep(0.25)
            if stop_event.is_set():
                return
            if config.lead_clips > 0:
                print(f"Lead ready ({queue.qsize()} segments). Starting playback.", flush=True)

            while not stop_event.is_set():
                if queue.empty() and producer_done.is_set():
                    break
                item = await queue.get()
                if item is None:
                    break
                if item.path:
                    played_clips.append(item.path)
                langfuse_event(
                    "playback_start",
                    metadata={"turn": item.turn, "clip_path": item.path},
                )
                # The player holds the item for its duration; when the next
                # one is not ready yet it keeps showing the last frame.
                await player.play(item)
                langfuse_event(
                    "playback_end",
                    metadata={"turn": item.turn, "clip_path": item.path},
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live consumer crashed")
            stop_event.set()

    await player.start(title=story.title, session_id=session_id)
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
        await player.stop()
        await graph.drain(state)
        langfuse_flush()

        if played_clips:
            out = video_dir / "full_session.mp4"
            print("Saving full session video…", flush=True)
            saved = await _save_full_session(played_clips, out)
            if saved:
                print(f"Saved: {saved}", flush=True)
            else:
                print("Could not save session video.", flush=True)
    return state


def _playable(state: StoryState) -> Playable:
    """The viewer-facing views of the segment just produced. `path` and the
    real `duration` are filled in once a clip exists."""
    beat, shot, comm = state.current_beat, state.current_shot, state.current_commentary
    return Playable(
        turn=state.turn_number,
        duration=float(shot.duration_seconds) if shot else 5.0,
        user_input=state.user_input,
        narration=beat.narration if beat else "",
        voiceover=comm.voiceover if comm else "",
        on_screen=list(shot.on_screen) if shot else [],
        camera=shot.camera if shot else "",
        transition=state.current_transition,
        intervention=state.current_intervention,
        intervention_reason=state.current_intervention_reason,
        scene=state.context_brief,
    )


def _pace_expected(state: StoryState, expected_dur: float) -> None:
    """Pacing for a segment whose clip is not measured yet (pipelined render
    or text-only): assume it plays for its planned duration."""
    state.last_clip_duration = expected_dur
    state.audio_seconds_owed = 0.0
    if state.current_commentary and state.current_commentary.voiceover:
        # A line was produced this turn (rendered natively or logged): the
        # silence floor re-arms from here.
        state.silence_seconds = 0.0
    else:
        state.silence_seconds += expected_dur
