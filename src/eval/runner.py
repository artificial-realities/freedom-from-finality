"""The run loops.

`run_live` is the film: a producer emitting and rendering clips just in
time and a `Player` (the web player or an ffplay window) showing them
while the viewer steers mid-stream. `run_play` prompts in the terminal between segments, for
headless development without a player.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from src.util.timing_log import TimingLog, git_revision

# Just-in-time rendering: the render and Director estimates are scaled by
# JIT_SAFETY and JIT_MARGIN_S is kept in hand (plus the config's
# render_slack_seconds), so a slower than usual call still has the clip
# on air in time.
JIT_SAFETY = 1.25
JIT_MARGIN_S = 0.5

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


def _start_timing(
    config: Config, story: Story, session_id: str, session_dir: Path, *, loop: str, **fields,
) -> TimingLog:
    """Open the session's timing log and write what the run is: code
    revision, config and the clip length every segment is planned for."""
    timing = TimingLog(session_dir / "timing.jsonl")
    revision, dirty = git_revision()
    timing.write(
        "session_start", session=session_id, loop=loop, config=config.name, story=story.title,
        revision=revision, dirty=dirty, llm_backend=config.llm_backend,
        director_model=config.director_model or config.model,
        director_service_tier=config.director_service_tier or None,
        video=config.video_enabled, i2v_backend=config.i2v_backend if config.video_enabled else None,
        clip_seconds=config.clip_seconds or config.i2v_duration,
        **fields,
    )
    return timing


def _line_fields(text: str) -> dict:
    """A viewer line as the timing log keeps it: its length and a short
    hash (the transcript has the words)."""
    return {"chars": len(text), "sha": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}


def _log_emit(timing: TimingLog, graph, turn: int) -> None:
    """The clip the engine just emitted, from its event record."""
    ev = graph.world.events[-1] if graph.world is not None and graph.world.events else None
    if ev is None or ev.segment != turn:
        return
    timing.write("emit", turn=turn, beat=ev.beat, clip=ev.clip_index, of=ev.clip_count,
                 seconds=ev.duration, voiceover=bool(ev.voiceover))


def _check_reference(i2v: I2VBackend | None, seed_image: str, timing: TimingLog) -> None:
    """Validate the reference against the render frame before anything
    renders (RUN-11); the verdict goes to the timing log. A strict
    refusal stops the run here, not at the first render."""
    if i2v is None:
        return
    try:
        verdict = i2v.check_reference(seed_image)
    except Exception as exc:
        timing.write("reference", image=seed_image, refused=str(exc))
        raise
    if verdict:
        timing.write("reference", image=seed_image, **verdict)


def _maybe_tts(config: Config, log_dir: Path | str) -> ElevenLabsTTS | None:
    if not config.audio_enabled:
        return None
    audio_dir = Path(log_dir) / "audio"
    return ElevenLabsTTS(
        voice_id=config.elevenlabs_voice_id,
        audio_dir=audio_dir,
    )


def seed_image_for(config: Config, story: Story) -> str:
    """The reference image the first clip is rendered from: the story's
    `seed_image`, or the config's `i2v_seed_image` for a story without one."""
    return story.seed_image or config.i2v_seed_image


def _maybe_i2v(config: Config, log_dir: Path | str, session_id: str = "") -> I2VBackend | None:
    """The run's render backend. Its seeds are derived from
    `ltxv_seed_key`, or the session id when that is empty (LTX-09)."""
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
        seed_key=config.ltxv_seed_key or session_id,
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
    i2v = _maybe_i2v(config, log_dir, session_id)

    session_dir = Path(log_dir) / "sessions" / session_id
    timing = _start_timing(config, story, session_id, session_dir, loop="play")
    seed_image: str = seed_image_for(config, story)
    _check_reference(i2v, seed_image, timing)
    graph = build_graph(
        config.graph, llm=llm, config=config, tts=tts,
        session_dir=session_dir, timing=timing,
    )

    ui = ui or TerminalUI()

    video_dir = Path(log_dir) / "video"
    frames_dir = video_dir / "frames"

    trace_tags = [config.name, "play"]

    turn_number = 0
    line_no = 0
    try:
        while True:
            turn_number += 1
            user_input = await ui.prompt_for_input()
            state.turn_number = turn_number
            state.user_input = user_input
            line = user_input.strip()
            if line:
                line_no += 1
                timing.write("line_received", line=line_no, **_line_fields(line))
                timing.write("line_taken", turn=turn_number, lines=[line_no])

            with turn_span(
                turn_number=turn_number,
                session_id=session_id,
                tags=trace_tags,
                user_input=user_input,
                metadata={"config": config.name, "user_input": user_input},
            ):
                result = await graph.ainvoke(state)
                state = _coerce_state(result)
                _log_emit(timing, graph, turn_number)
                if line:
                    timing.write("line_landed", turn=turn_number, lines=[line_no])

                if i2v is not None and state.current_shot is not None:
                    timing.write("fire", turn=turn_number, seconds=state.current_shot.duration_seconds)
                    t0 = time.monotonic()
                    seed_image, _silent, _playable = await _render_turn(
                        state=state,
                        i2v=i2v,
                        seed_image=seed_image,
                        frames_dir=frames_dir,
                        video_dir=video_dir,
                    )
                    timing.write("render_done", turn=turn_number, ok=_silent is not None,
                                 render_s=round(time.monotonic() - t0, 4), server=dict(i2v.last_render))

                set_trace_output(_trace_output(state))

            _commit_history(state)
            _log_story_turn(story_logger, state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n(quit)")
    finally:
        await graph.drain(state)
        timing.write("session_end", turn=state.turn_number)
        timing.close()
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
    """Live loop: a producer generating clips, a player showing them.

    The producer emits one clip per segment from the Director's current
    beat and pushes each rendered clip onto a bounded queue (size =
    `lead_clips + 1`). The consumer hands them to the player one at a
    time; the player holds each for its duration, so the server is the
    clock. `max_segments` stops the producer after that many clips
    (tests, bounded demos); otherwise it runs until Ctrl+C.

    Just in time: once playback runs, a clip's render is fired as late as
    it safely can be (the render estimate times a safety factor, a
    margin and `render_slack_seconds` before the clip is due on air), and
    the Director is asked for the next beat only when the current one has
    no clip left, as late as the render allows. Until then the producer
    listens for the viewer. A line cuts the beat in progress short (its
    unrendered clips are dropped), cancels a Director call in flight, and
    the Director writes the next beat from that line at once, so the line
    reaches the earliest clip that is not yet rendering. Before playback
    starts the producer runs flat out to build the lead.

    With a video backend each item is a rendered clip. Without one it is
    the clip's text, held for the clip's duration, so a headless config
    gives a working session in the web player.

    The run's timing (fires, renders, the queue, air time, viewer lines,
    Director calls, the page's beacons) goes to
    `logs/sessions/<id>/timing.jsonl`; see `src.util.timing_log`.
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
    i2v = _maybe_i2v(config, log_dir, session_id)
    player = player or TerminalPlayer()

    queue_size = max(1, config.lead_clips + 1)
    session_dir = Path(log_dir) / "sessions" / session_id
    timing = _start_timing(
        config, story, session_id, session_dir, loop="live",
        lead_clips=config.lead_clips, queue_size=queue_size,
        render_slack_seconds=config.render_slack_seconds, jit_safety=JIT_SAFETY,
        jit_margin=JIT_MARGIN_S, time_scale=player.time_scale,
    )
    _check_reference(i2v, seed_image_for(config, story), timing)
    graph = build_graph(
        config.graph, llm=llm, config=config, tts=tts,
        session_dir=session_dir, timing=timing,
    )
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
    lead_ready = asyncio.Event()   # the lead is built: playback may start
    played_clips: list[str] = []
    played_turns: list[int] = []
    # The player's clock as the producer sees it: which clip is on air,
    # when it started (monotonic) and how long it holds, plus the rendered
    # clips waiting behind it. Durations are in real seconds (story seconds
    # over the player's time scale).
    air: dict = {"turn": None, "start": None, "hold": 0.0}
    queued_holds: list[float] = []
    # Viewer lines as the timing log follows them, by the player's line id:
    # taken into a Director call, then carried by the planned beat until
    # its first clip is emitted (landed) or the beat is cut first (dropped).
    plan_lines: list[int] = []
    beat_lines: list[int] = []

    def _on_line(line_id: int, text: str) -> None:
        into = time.monotonic() - air["start"] if air["start"] is not None else None
        timing.write("line_received", line=line_id, on_air=air["turn"],
                     into=round(into, 4) if into is not None else None, **_line_fields(text))

    player.on_line = _on_line
    player.on_beacon = lambda record: timing.write("beacon", **record)

    def _hold_of(item: Playable) -> float:
        return float(item.duration) / player.time_scale

    # The producer mutates these state fields directly so the next turn's
    # voice-over gate sees up-to-date pacing. Mark the loop as managing
    # pacing and open the gate for turn 1 by setting silence at the floor.
    state.pacing_managed = True
    state.silence_seconds = config.min_pause_seconds
    state.audio_seconds_owed = 0.0

    trace_tags = [config.name, "live"]

    # A backend that owns continuity (the LTX-2.5 server's sliding window)
    # depends only on the prompt, so render(N) can overlap the Director.
    _pipeline_ok = i2v is not None and i2v.manages_continuity

    # Running averages of the measured render and Director times, for the
    # just-in-time schedule. None until the first measurement.
    est: dict[str, float | None] = {"render": None, "director": None}

    def _learn(key: str, seconds: float) -> None:
        prev = est[key]
        est[key] = seconds if prev is None else 0.5 * prev + 0.5 * seconds

    async def _enqueue(item: Playable) -> None:
        await queue.put(item)
        # How long before it goes on air the clip is ready (negative: the
        # picture is already holding for it). None while the lead builds.
        margin = (air["start"] + air["hold"] + sum(queued_holds) - time.monotonic()
                  if air["start"] is not None else None)
        queued_holds.append(_hold_of(item))
        timing.write("enqueued", turn=item.turn, duration=item.duration, queued=queue.qsize(),
                     margin=round(margin, 4) if margin is not None else None)
        if queue.qsize() >= queue_size:
            lead_ready.set()
        player.prepare(item)

    async def _bg_render_and_enqueue(
        item: Playable, *, prompt: str, seed_image: str, duration: int | None,
    ) -> None:
        """Render one clip in the background and enqueue it."""
        assert i2v is not None
        t0 = time.monotonic()
        video_path = await i2v.synthesize(
            image_path=seed_image, prompt=prompt, turn=item.turn, duration=duration,
        )
        render_s = time.monotonic() - t0
        _learn("render", render_s)
        timing.write("render_done", turn=item.turn, ok=bool(video_path), render_s=round(render_s, 4),
                     server=dict(i2v.last_render))
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
        seed_image = seed_image_for(config, story)
        turn = 0
        clip_hold = float(config.clip_seconds or config.i2v_duration) / player.time_scale
        # Span-pending: collects silent clips behind a voiceover whose audio
        # spills past one clip. Closed (concat+mux+enqueue) once the
        # accumulated clip duration covers the audio.
        # Shape: {"item": Playable, "clips": [paths], "audio": str,
        #         "owed": float, "audio_dur": float, "start_turn": int}
        pending_span: dict | None = None
        # Background render task for pipelining.
        _prev_render: asyncio.Task | None = None
        # The viewer text the last plan answered ("" when it answered none),
        # while its beat has not emitted a clip: a line arriving then is
        # joined to it (ARCH-06).
        planned_text = ""

        async def _drain_prev() -> None:
            nonlocal _prev_render
            if _prev_render is not None:
                try:
                    await _prev_render
                except Exception:
                    logger.warning("Pipelined render failed", exc_info=True)
                _prev_render = None

        def _needed_at() -> float | None:
            """Monotonic time at which the next clip to render is due on
            air; None before playback starts."""
            if air["start"] is None:
                return None
            due = air["start"] + air["hold"] + sum(queued_holds)
            if _prev_render is not None and not _prev_render.done():
                due += clip_hold  # the render in flight takes the next slot
            return due

        async def _take() -> str:
            """Everything the viewer typed since the last call. The lines
            join the next plan."""
            text = await player.next_input()
            if player.taken:
                plan_lines.extend(player.taken)
                timing.write("line_taken", turn=state.turn_number, lines=list(player.taken))
            return text

        def _abort(reason: str) -> int:
            """Cut the current beat short; lines its plan carried and no clip
            has shown yet are dropped with it."""
            dropped = graph.abort_beat(reason)
            if beat_lines:
                timing.write("line_dropped", turn=state.turn_number, lines=list(beat_lines),
                             reason="the beat that carried them was cut before its first clip")
                beat_lines.clear()
            return dropped

        async def _plan(text: str) -> None:
            """Ask the Director for the next beat from `text`. A line that
            arrives while the call runs cancels it, and the call restarts
            with everything the viewer typed."""
            nonlocal state, planned_text
            while True:
                state.user_input = text
                t0 = time.monotonic()
                call = asyncio.create_task(graph.plan(state))
                waiter = asyncio.create_task(player.wait_input(None))
                done, _ = await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
                if call in done:
                    waiter.cancel()
                    await call
                    _learn("director", time.monotonic() - t0)
                    beat_lines[:] = plan_lines
                    plan_lines.clear()
                    planned_text = text
                    return
                call.cancel()
                try:
                    await call
                except asyncio.CancelledError:
                    pass
                graph.discard_pending_input(state.turn_number)
                more = await _take()
                text = " ".join(x for x in (text, more) if x)
                player.status(f"you spoke while the Director was writing; it starts segment {state.turn_number} again")

        async def _answer(text: str, turn: int) -> None:
            """A viewer line arrived before segment `turn` was emitted: cut
            the beat in progress short and plan from the line. When the
            planned beat has not played a clip and its plan answered an
            earlier line (the second line of the pair arrived after the
            Director returned), that earlier line is joined to this one
            and planned once, as a line that cancels a call in flight is:
            its recorded intervention is dropped so the pair is recorded
            once, with one disposition (ARCH-06)."""
            if graph.beat_unplayed() and planned_text:
                graph.discard_pending_input(state.turn_number)
                plan_lines[:0] = beat_lines     # the earlier line travels with this one
                beat_lines.clear()
                text = f"{planned_text} {text}"
                player.status("you spoke again before the Director's answer played; it answers both lines")
            dropped = _abort("the viewer spoke") if graph.has_next_clip() else 0
            if dropped:
                player.status(f"your line cut the beat short; {dropped} planned clip(s) dropped")
            player.status(f"the Director is writing segment {turn}")
            await _plan(text)

        async def _listen(turn: int) -> None:
            """Wait for the just-in-time moment of segment `turn`, taking the
            viewer's lines as they come: a line cuts the beat short and
            has the Director write the next one at once."""
            while True:
                need = _needed_at()
                if need is None:
                    break  # building the lead: as fast as possible
                fire_at = need - (est["render"] or 0.0) * JIT_SAFETY - JIT_MARGIN_S - config.render_slack_seconds
                if graph.has_next_clip():
                    wait_until = fire_at
                    player.status(f"listening for your input before segment {turn}")
                else:
                    wait_until = fire_at - (est["director"] or 0.0) * JIT_SAFETY - JIT_MARGIN_S
                    player.status(f"listening for your input before the Director writes from segment {turn}")
                timeout = wait_until - time.monotonic()
                if timeout <= 0:
                    break
                if not await player.wait_input(timeout):
                    break  # time is up
                text = await _take()
                if not text:
                    continue
                await _answer(text, turn)
            # The moment has come. Anything typed since is taken now.
            if not graph.has_next_clip():
                player.status(f"the Director is writing segment {turn}")
                await _plan(await _take())
            elif player.has_input():
                text = await _take()
                if text:
                    await _answer(text, turn)

        try:
            while not stop_event.is_set():
                if max_segments is not None and turn >= max_segments:
                    break
                turn += 1
                state.turn_number = turn

                with turn_span(
                    turn_number=turn,
                    session_id=session_id,
                    tags=trace_tags,
                    user_input="",
                    metadata={"config": config.name},
                ):
                    await _listen(turn)
                    state = await graph.emit(state)
                    _log_emit(timing, graph, turn)
                    if beat_lines:
                        timing.write("line_landed", turn=turn, lines=list(beat_lines))
                        beat_lines.clear()

                    silent: str | None = None
                    handled = False
                    item: Playable | None = None
                    if state.current_shot is not None:
                        item = _playable(state)
                        if i2v is None:
                            # ── Text-only film ──────────────────────────
                            # No renderer: the clip plays as text for its
                            # own duration. Pacing follows that clock.
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
                            # the background.
                            handled = True
                            await _drain_prev()
                            player.status(f"rendering segment {turn}")
                            shot_dur = state.current_shot.duration_seconds
                            # How long until this clip is due on air (None
                            # while the lead builds): the render's budget.
                            need = _needed_at()
                            timing.write("fire", turn=turn, seconds=shot_dur,
                                         due_in=round(need - time.monotonic(), 4) if need is not None else None)
                            _prev_render = asyncio.create_task(
                                _bg_render_and_enqueue(
                                    item,
                                    prompt=state.current_shot.i2v_prompt,
                                    seed_image=seed_image,
                                    duration=shot_dur,
                                )
                            )
                            # Set pacing state now with the expected clip
                            # duration so the next voice-over decision is right.
                            _pace_expected(state, float(shot_dur))
                        else:
                            # ── Serial path (audio / span) ──
                            await _drain_prev()
                            player.status(f"rendering segment {turn}")
                            need = _needed_at()
                            timing.write("fire", turn=turn, seconds=state.current_shot.duration_seconds,
                                         due_in=round(need - time.monotonic(), 4) if need is not None else None)
                            t0 = time.monotonic()
                            seed_image, silent, _ = await _render_turn(
                                state=state,
                                i2v=i2v,
                                seed_image=seed_image,
                                frames_dir=frames_dir,
                                video_dir=video_dir,
                                mux_inline=False,  # live loop owns muxing
                            )
                            timing.write("render_done", turn=turn, ok=silent is not None,
                                         render_s=round(time.monotonic() - t0, 4), server=dict(i2v.last_render))

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
        except Exception as exc:
            logger.exception("Live producer crashed on turn %s", turn)
            timing.write("error", where="producer", turn=turn, error=repr(exc))
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
                try:
                    await asyncio.wait_for(lead_ready.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass
            if stop_event.is_set():
                return
            if config.lead_clips > 0:
                print(f"Lead ready ({queue.qsize()} segments). Starting playback.", flush=True)

            while not stop_event.is_set():
                if queue.empty() and producer_done.is_set():
                    break
                # Time spent here after a hold ended is time the viewer
                # watched a held frame: a stall.
                asked = time.monotonic()
                item = await queue.get()
                waited = time.monotonic() - asked
                if item is None:
                    break
                if queued_holds:
                    queued_holds.pop(0)
                if item.path:
                    played_clips.append(item.path)
                langfuse_event(
                    "playback_start",
                    metadata={"turn": item.turn, "clip_path": item.path},
                )
                # The player holds the item for its duration; when the next
                # one is not ready yet it keeps showing the last frame.
                air["turn"] = item.turn
                air["start"] = time.monotonic()
                air["hold"] = _hold_of(item)
                timing.write("playing", turn=item.turn, duration=item.duration,
                             hold=round(air["hold"], 4), waited=round(waited, 4),
                             first=not played_turns)
                played_turns.append(item.turn)
                await player.play(item)
                timing.write("hold_end", turn=item.turn)
                langfuse_event(
                    "playback_end",
                    metadata={"turn": item.turn, "clip_path": item.path},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Live consumer crashed")
            timing.write("error", where="consumer", error=repr(exc))
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
        timing.write("session_end", turn=state.turn_number, played=len(played_turns))
        timing.close()
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
        intervention=state.current_intervention,
        intervention_reason=state.current_intervention_reason,
        scene=state.context_brief,
        beat=state.current_beat_intent,
        clip=state.current_clip,
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
