"""Scenario runner — drives a config through a list of user commands.

Used both for benchmark runs (no UI, bypass buffer) and for `play
--scenario` which can optionally reuse the same loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import sys
import threading

from src.graph import build_graph
from src.i2v import build_i2v_backend, extract_last_frame
from src.i2v.base import I2VBackend
from src.llm import build_backend
from src.models.config import Config
from src.models.story import Story
from src.playback import mux_audio_into_video, play_clip
from src.state.story_state import HistoryEntry, StoryState
from src.tts.elevenlabs import ElevenLabsTTS
from src.ui.terminal import TerminalUI
from src.util.interaction_logger import InteractionLogger

logger = logging.getLogger(__name__)


def load_scenario(path: Path | str) -> list[str]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def run_scenario(
    *,
    config: Config,
    story: Story,
    scenario_path: Path | str,
    log_dir: Path | str = "logs",
    ui: TerminalUI | None = None,
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

    if ui is not None:
        ui.render_opening(state)

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
            seed_image, _playable_path = await _render_turn(
                state=state,
                i2v=i2v,
                seed_image=seed_image,
                frames_dir=frames_dir,
                video_dir=video_dir,
                interaction_logger=interaction_logger,
            )

        _commit_history(state)

        if ui is not None:
            ui.render_turn(state)

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
    )


async def _render_turn(
    *,
    state: StoryState,
    i2v: I2VBackend,
    seed_image: str,
    frames_dir: Path,
    video_dir: Path,
    interaction_logger: InteractionLogger,
) -> tuple[str, str | None]:
    """Render one clip, optionally mux audio onto it, return:
        (next_seed_image_path, playable_path_or_None)

    `playable_path` is the muxed mp4 if Attenborough's TTS produced
    audio AND ffmpeg muxed it cleanly; otherwise the silent DashScope
    mp4; otherwise None when nothing rendered.

    On any i2v failure we fall back to reusing the current seed — the
    next turn re-renders against the same anchor rather than crashing.
    """
    if not Path(seed_image).exists():
        logger.warning("Seed image missing for turn %s: %s — skipping render",
                       state.turn_number, seed_image)
        interaction_logger.log_event("i2v_skip", state.turn_number,
                                     {"reason": "missing_seed", "seed_image": seed_image})
        return seed_image, None

    prompt = state.current_shot.i2v_prompt
    video_path = await i2v.synthesize(
        image_path=seed_image,
        prompt=prompt,
        turn=state.turn_number,
    )
    if not video_path:
        interaction_logger.log_event("i2v_render_failed", state.turn_number,
                                     {"seed_image": seed_image})
        return seed_image, None

    next_seed = frames_dir / f"turn_{state.turn_number:04d}_last.png"
    extracted = await asyncio.to_thread(extract_last_frame, video_path, next_seed)

    # Optional audio mux — only if Attenborough produced TTS this turn.
    playable_path = video_path
    muxed_path: str | None = None
    if state.current_audio_path:
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
    return extracted or seed_image, playable_path


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
    ui.render_opening(state)

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
            ui.render_turn(state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        ui.render_error("\n(quit)")
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
    ui: TerminalUI | None = None,
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

    ui = ui or TerminalUI()
    ui.render_opening(state)
    ui.console.print(
        f"[dim]Pre-buffering up to {config.video_buffer_clips} clip(s); "
        f"first render takes ~30s on DashScope. Type to steer; Ctrl+C to quit.[/dim]"
    )

    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    loop = asyncio.get_running_loop()
    user_input_queue: asyncio.Queue[str] = asyncio.Queue()
    clip_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, config.video_buffer_clips))

    stop_event = asyncio.Event()

    def _stdin_loop() -> None:
        """Blocking stdin reader running on a daemon thread."""
        while not stop_event.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:  # EOF
                return
            line = line.strip()
            asyncio.run_coroutine_threadsafe(user_input_queue.put(line), loop)

    threading.Thread(target=_stdin_loop, daemon=True).start()

    async def producer() -> None:
        nonlocal state
        seed_image = config.i2v_seed_image
        turn = 0
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

                playable: str | None = None
                if state.current_shot is not None:
                    seed_image, playable = await _render_turn(
                        state=state,
                        i2v=i2v,
                        seed_image=seed_image,
                        frames_dir=frames_dir,
                        video_dir=video_dir,
                        interaction_logger=interaction_logger,
                    )

                _commit_history(state)
                ui.render_turn(state)

                if playable:
                    await clip_queue.put(playable)  # blocks if buffer is full
                else:
                    interaction_logger.log_event(
                        "live_no_playable", turn,
                        {"reason": "render_failed_or_skipped"},
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live producer crashed on turn %s", turn)
            stop_event.set()

    async def consumer() -> None:
        try:
            while not stop_event.is_set():
                clip_path = await clip_queue.get()
                interaction_logger.log_event(
                    "playback_start", state.turn_number, {"clip_path": clip_path},
                )
                exit_code = await play_clip(clip_path)
                interaction_logger.log_event(
                    "playback_end", state.turn_number,
                    {"clip_path": clip_path, "exit_code": exit_code},
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live consumer crashed")
            stop_event.set()

    producer_task = asyncio.create_task(producer())
    consumer_task = asyncio.create_task(consumer())

    try:
        await asyncio.gather(producer_task, consumer_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        ui.render_error("\n(quit)")
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
    return state
