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
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from src.graph import build_graph
from src.graph.director_engine import EmitFailed
from src.i2v import build_i2v_backend
from src.i2v.base import I2VBackend
from src.i2v.shape import DEFAULT_FPS, ClipShape, ShapeRegistry
from src.llm import build_backend
from src.models.config import Config
from src.models.story import Story
from src.playback import Playable, Player, TerminalPlayer
from src.state.story_state import HistoryEntry, StoryState
from src.ui.terminal import TerminalUI
from src.util.langfuse_setup import event as langfuse_event
from src.util.langfuse_setup import flush as langfuse_flush
from src.util.langfuse_setup import set_trace_output, turn_span
from src.util.media import probe_duration
from src.util.story_log import StoryLogger
from src.util.timing_log import TimingLog, git_revision

# Just-in-time rendering: a render fires its estimate plus the config's
# `buffer_seconds` before its clip is due on air, so the clip is ready that
# long before it plays. The Director's estimate is scaled by
# JIT_SAFETY and JIT_MARGIN_S is kept in hand, so a slower than usual call
# still leaves the render its moment.
JIT_SAFETY = 1.25
JIT_MARGIN_S = 0.5
# Fail-soft: a failed render is retried once only if the retry can
# still finish before its clip is due (the p95 of the last RENDER_WINDOW
# renders plus JIT_MARGIN_S); this many failed segments in a row stop the
# film instead of looping on a renderer that is down.
RENDER_WINDOW = 20
MAX_CONSECUTIVE_FAILURES = 3
# A `fire_due` record is written again only when the due moment moves by more than this.
FIRE_DUE_STEP_S = 0.2

logger = logging.getLogger(__name__)


def _make_session_id(config_name: str, label: str) -> str:
    """Stable session id used for Langfuse and the on-disk Markdown log."""
    return f"{config_name}_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _trace_output(state: StoryState) -> dict:
    """What to surface as the trace.output in the Langfuse Sessions view."""
    return {
        "narration": state.current_beat.narration if state.current_beat else None,
        "dialogue": state.current_dialogue or None,
    }


def _start_timing(
    config: Config, story: Story, session_id: str, session_dir: Path, *, loop: str,
    shapes: ShapeRegistry, **fields,
) -> TimingLog:
    """Open the session's timing log and write what the run is: code
    revision, config and the clip shapes (the default is every clip's)."""
    timing = TimingLog(session_dir / "timing.jsonl")
    revision, dirty = git_revision()
    timing.write(
        "session_start", session=session_id, loop=loop, config=config.name, story=story.title,
        revision=revision, dirty=dirty, llm_backend=config.llm_backend,
        director_model=config.director_model or config.model,
        director_service_tier=config.director_service_tier or None,
        director_reasoning_effort=config.director_reasoning_effort or None,
        video=config.video_enabled, i2v_backend=config.i2v_backend if config.video_enabled else None,
        clip_seconds=round(shapes.default.delivered_seconds, 4), shape=shapes.default.record(),
        shapes=shapes.frames,
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
    timing.write("emit", turn=turn, clip_id=ev.clip_id, beat=ev.beat, clip=ev.clip_index, of=ev.clip_count,
                 seconds=ev.duration, speech=bool(ev.dialogue))


def _shapes_for(config: Config, i2v: I2VBackend | None) -> ShapeRegistry:
    """The run's clip shape, at the renderer's frame rate."""
    return ShapeRegistry.from_config(config, fps=i2v.frame_rate if i2v is not None else DEFAULT_FPS)


def _check_shapes(i2v: I2VBackend | None, shapes: ShapeRegistry, timing: TimingLog,
                  config: Config | None = None) -> None:
    """Refuse the run before anything renders when the renderer has not
    warmed every registered shape; the verdict goes to the log. A server
    whose loudness ceiling differs from the config's is run, with a
    warning: the film plays either way."""
    if i2v is None:
        return
    try:
        verdict = i2v.check_shapes(shapes)
    except Exception as exc:
        timing.write("shapes", frames=shapes.frames, refused=str(exc))
        raise
    if verdict:
        timing.write("shapes", **verdict)
        if config is not None and "audio_ceiling_lufs" in verdict:
            want, have = config.audio_ceiling_lufs, verdict["audio_ceiling_lufs"]
            if (want is None) != (have is None) or (want is not None and abs(want - have) > 0.05):
                logger.warning("The server's loudness ceiling is %s; the config asks for %s", have, want)
                timing.write("ceiling_mismatch", config=want, server=have)


def _check_reference(i2v: I2VBackend | None, seed_image: str, timing: TimingLog) -> None:
    """Validate the reference against the render frame before anything
    renders; the verdict goes to the timing log. A strict
    refusal stops the run here, not at the first render, and so does a
    story without a reference: the renderer starts every film from one."""
    if i2v is None:
        return
    try:
        if not seed_image:
            raise ValueError("this story has no reference image (`picture.reference`); the renderer starts "
                             "the film from one, so a video run needs it")
        verdict = i2v.check_reference(seed_image)
    except Exception as exc:
        timing.write("reference", image=seed_image, refused=str(exc))
        raise
    if verdict:
        timing.write("reference", image=seed_image, **verdict)


def _maybe_i2v(config: Config, log_dir: Path | str, session_id: str = "") -> I2VBackend | None:
    """The run's render backend. Its seeds are derived from
    `ltxv_seed_key`, or the session id when that is empty."""
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
        restart_recovery=config.ltxv_restart_recovery,
        # A secret, so it comes from the environment, never from a config file.
        api_key=os.getenv("LTX2_API_KEY", "").strip(),
    )


async def _render_turn(*, state: StoryState, i2v: I2VBackend, seed_image: str) -> tuple[str, str | None]:
    """Render one clip for the turn-by-turn loop (`run_play`), threading
    the seed image forward.

    Returns `(next_seed_image, video_path)`: the seed image is always set
    (the current one on failure); the video is None when the render failed.
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
        return seed_image, None

    prompt = state.current_shot.i2v_prompt
    video_path = await i2v.synthesize(
        image_path=seed_image,
        prompt=prompt,
        turn=state.turn_number,
        shape=state.current_shape,
        clip_id=state.current_clip_id or None,
        parent_clip_id=state.current_parent_clip_id if state.current_clip_id else None,
    )
    if not video_path:
        langfuse_event(
            "i2v_render_failed",
            metadata={"turn": state.turn_number, "seed_image": seed_image},
            level="ERROR",
        )
        return seed_image, None

    # The LTXV backend owns continuity server-side (sliding window in GPU
    # memory), so the seed image stays the reference.
    langfuse_event(
        "i2v_render",
        metadata={"turn": state.turn_number, "seed_image": seed_image, "video_path": video_path},
    )
    return seed_image, video_path


def _coerce_state(result) -> StoryState:
    """LangGraph may hand us either a Pydantic StoryState or a dict."""
    if isinstance(result, StoryState):
        return result
    if isinstance(result, dict):
        return StoryState(**result)
    raise TypeError(f"Unexpected graph output type: {type(result)!r}")


def _commit_history(state: StoryState) -> None:
    """Append the committed turn to history, if both views exist."""
    if state.current_beat is None or state.current_shot is None:
        logger.warning("Turn %s skipped history commit — missing beat or shot", state.turn_number)
        return
    state.history.append(HistoryEntry(
        turn=state.turn_number, user_input=state.user_input, beat=state.current_beat, shot=state.current_shot,
    ))


def _log_story_turn(story_logger: StoryLogger, state: StoryState) -> None:
    story_logger.log_turn(
        turn=state.turn_number,
        user_input=state.user_input,
        beat=state.current_beat,
        shot=state.current_shot,
        dialogue=state.current_dialogue,
        world_state=state.world_state,
        context_brief=state.context_brief,
        extra=state.turn_notes,
    )


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
    i2v = _maybe_i2v(config, log_dir, session_id)

    session_dir = Path(log_dir) / "sessions" / session_id
    shapes = _shapes_for(config, i2v)
    timing = _start_timing(config, story, session_id, session_dir, loop="play", shapes=shapes)
    seed_image = story.reference_image
    _check_reference(i2v, seed_image, timing)
    _check_shapes(i2v, shapes, timing, config)
    graph = build_graph(config.graph, llm=llm, config=config, session_dir=session_dir, timing=timing, shapes=shapes)

    ui = ui or TerminalUI()

    trace_tags = [config.name, "play"]

    turn_number = 0
    line_no = 0
    carried = ""        # a line whose answering Director call failed
    try:
        while True:
            turn_number += 1
            user_input = await ui.prompt_for_input()
            if carried:
                user_input = " ".join(x for x in (carried, user_input) if x.strip())
                carried = ""
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
                if line and getattr(graph, "last_plan_fallback", False):
                    carried = user_input
                    timing.write("line_requeued", turn=turn_number, lines=[line_no],
                                 reason="the Director call that answered it failed")
                if line:
                    timing.write("line_landed", turn=turn_number, lines=[line_no])

                if i2v is not None and state.current_shot is not None:
                    timing.write("fire", turn=turn_number, seconds=state.current_shot.duration_seconds,
                                 frames=state.current_shape.render_frames)
                    t0 = time.monotonic()
                    seed_image, video = await _render_turn(state=state, i2v=i2v, seed_image=seed_image)
                    timing.write("render_done", turn=turn_number, ok=video is not None,
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
    `startup_clips + 1`). The consumer hands them to the player one at a
    time; the player holds each for its duration, so the server is the
    clock. `max_segments` stops the producer after that many clips
    (tests, bounded demos); otherwise it runs until Ctrl+C.

    Just in time: once playback runs, a clip's render is fired as late as
    it safely can be (the render estimate times a safety factor, a
    `buffer_seconds`, the steady-state buffer, before the clip is due on air), and
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
    i2v = _maybe_i2v(config, log_dir, session_id)
    player = player or TerminalPlayer()

    queue_size = max(1, config.startup_clips + 1)
    session_dir = Path(log_dir) / "sessions" / session_id
    shapes = _shapes_for(config, i2v)
    timing = _start_timing(
        config, story, session_id, session_dir, loop="live", shapes=shapes,
        startup_clips=config.startup_clips, queue_size=queue_size,
        buffer_seconds=config.buffer_seconds, jit_safety=JIT_SAFETY,
        jit_margin=JIT_MARGIN_S, time_scale=player.time_scale,
    )
    _check_reference(i2v, story.reference_image, timing)
    _check_shapes(i2v, shapes, timing, config)
    graph = build_graph(config.graph, llm=llm, config=config, session_dir=session_dir, timing=timing, shapes=shapes)
    mode = "video" if i2v is not None else "text (no video backend)"
    if config.startup_clips > 0:
        print(f"Live, {mode}: pre-buffering {queue_size} segments before playback "
              f"(startup_clips={config.startup_clips}). Ctrl+C to quit.", flush=True)
    else:
        print(f"Live, {mode}: playback starts as soon as the first segment is ready. "
              "Ctrl+C to quit.", flush=True)

    video_dir = Path(log_dir) / "video"

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

    trace_tags = [config.name, "live"]

    # Running averages of the measured render and Director times, for the
    # just-in-time schedule. None until the first measurement.
    est: dict[str, float | None] = {"render": None, "director": None}

    def _learn(key: str, seconds: float) -> None:
        prev = est[key]
        est[key] = seconds if prev is None else 0.5 * prev + 0.5 * seconds

    def _render_estimate() -> float:
        return est["render"] or 0.0

    def _director_lead() -> float:
        """How long before the fire the Director call for a new beat starts."""
        return (est["director"] or 0.0) * JIT_SAFETY + JIT_MARGIN_S

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

    renders: list[float] = []     # the last RENDER_WINDOW successful render walls

    def _render_p95() -> float:
        if not renders:
            return est["render"] or 0.0
        ordered = sorted(renders)
        return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]

    async def _bg_render_and_enqueue(
        item: Playable, *, prompt: str, seed_image: str, shape: ClipShape,
        clip_id: int | None = None, parent_clip_id: int | None = None,
    ) -> bool:
        """Render one clip in the background and enqueue it. The
        player holds it for the shape's delivered frames; the file's
        measured length is only checked against the shape. A failed render
        is retried once when the retry can still land before the clip is
        due. Returns whether the clip was queued."""
        assert i2v is not None
        video_path = None
        for attempt in (1, 2):
            t0 = time.monotonic()
            video_path = await i2v.synthesize(
                image_path=seed_image, prompt=prompt, turn=item.turn, shape=shape,
                clip_id=clip_id, parent_clip_id=parent_clip_id,
            )
            render_s = time.monotonic() - t0
            timing.write("render_done", turn=item.turn, ok=bool(video_path), render_s=round(render_s, 4),
                         server=dict(i2v.last_render), attempt=attempt)
            if video_path:
                _learn("render", render_s)
                renders.append(render_s)
                del renders[:-RENDER_WINDOW]
                break
            langfuse_event("i2v_render_failed", metadata={"turn": item.turn, "attempt": attempt}, level="ERROR")
            if not config.fail_soft or attempt == 2:
                return False
            # The clip is due when everything on air and queued has played.
            due = (air["start"] + air["hold"] + sum(queued_holds)) if air["start"] is not None else None
            needs = _render_p95() + JIT_MARGIN_S
            left = due - time.monotonic() if due is not None else None
            retry = left is None or needs <= left
            timing.write("render_retry", turn=item.turn, retry=retry, needs_s=round(needs, 4),
                         left_s=round(left, 4) if left is not None else None)
            if not retry:
                return False
        if video_path:
            item.path = video_path
            # The overlap seam: the clip's head repeats the previous
            # clip's tail, over which the page cuts and crossfades, unless the
            # clip was seeded from the reference (the run's first, or a server
            # restart's), which repeats nothing.
            if config.seam == "overlap" and not i2v.last_render.get("reference"):
                item.overlap_seconds = shape.overlap_seconds
            if i2v.last_render.get("reseeded"):
                # A restart the client could not restore from the last clip's
                # tail: the Director hears the picture was reset.
                timing.write("reseeded", turn=item.turn)
                graph.note_reseed(item.turn)
            item.duration = shape.delivered_seconds
            measured = await probe_duration(item.path)
            if measured is not None and abs(measured - shape.render_seconds) > 1.5 / shape.fps:
                timing.write("shape_mismatch", turn=item.turn, frames=shape.render_frames,
                             expected_s=round(shape.render_seconds, 4), measured_s=round(measured, 4))
            await _enqueue(item)
            return True
        return False

    async def producer() -> None:
        nonlocal state
        seed_image = story.reference_image
        turn = 0
        # Background render task for pipelining, and the hold of the clip it
        # renders (its shape's delivered seconds on the player's clock).
        _prev_render: asyncio.Task | None = None
        _prev_hold = 0.0
        # The viewer text the last plan answered ("" when it answered none),
        # while its beat has not emitted a clip: a line arriving then is
        # joined to it.
        planned_text = ""
        # Fail-soft: viewer text whose answer was rolled back, for the next
        # plan; the lines that landed on the last clip; failed segments in
        # a row.
        carry_text = ""
        last_landed: tuple[int, list[int]] | None = None
        failures = 0

        def _fail(turn_failed: int, reason: str) -> None:
            """Count a failed segment; stop the film after too many in a row."""
            nonlocal failures
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                timing.write("error", where="producer", turn=turn_failed,
                             error=f"{failures} segments failed in a row ({reason}); stopping")
                player.status(f"{failures} segments failed in a row; the film stops")
                raise RuntimeError(f"{failures} segments failed in a row: {reason}")

        def _roll_back(turn_failed: int, reason: str) -> None:
            """The last emitted clip never reached the viewer: take it back
            and have the next plan start from the clip before."""
            nonlocal carry_text
            carry = graph.rollback_clip(turn_failed, reason)
            if state.history and state.history[-1].turn == turn_failed:
                state.history.pop()
            requeued = list(last_landed[1]) if last_landed and last_landed[0] == turn_failed else []
            requeued += beat_lines
            beat_lines.clear()
            if requeued:
                plan_lines[:0] = requeued
                timing.write("line_requeued", turn=turn_failed, lines=requeued,
                             reason="the clip that answered them never reached the screen")
            if carry:
                carry_text = " ".join(x for x in (carry, carry_text) if x)
            timing.write("rollback", turn=turn_failed, reason=reason)
            story_logger.log_note(f"Segment {turn_failed} never reached the viewer ({reason}); "
                                  "the world was rolled back to before it and the segment replanned.")
            player.status(f"segment {turn_failed} failed ({reason}); the Director replans from the clip before")

        async def _drain_prev() -> bool:
            """Wait for the render in flight; whether its clip was queued."""
            nonlocal _prev_render
            ok = True
            if _prev_render is not None:
                try:
                    ok = bool(await _prev_render)
                except Exception:
                    logger.warning("Pipelined render failed", exc_info=True)
                    ok = False
                _prev_render = None
            return ok

        def _needed_at() -> float | None:
            """Monotonic time at which the next clip to render is due on
            air; None before playback starts."""
            if air["start"] is None:
                return None
            due = air["start"] + air["hold"] + sum(queued_holds)
            if _prev_render is not None and not _prev_render.done():
                due += _prev_hold  # the render in flight takes the next slot
            return due

        async def _take() -> str:
            """Everything the viewer typed since the last call, after any line
            whose answer never reached the screen (a rolled-back clip). The
            lines join the next plan."""
            nonlocal carry_text
            text = await player.next_input()
            if player.taken:
                plan_lines.extend(player.taken)
                timing.write("line_taken", turn=state.turn_number, lines=list(player.taken))
            if carry_text:
                text = " ".join(x for x in (carry_text, text) if x)
                carry_text = ""
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
            nonlocal state, planned_text, carry_text
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
                    if getattr(graph, "last_plan_fallback", False):
                        # The call answering the line failed and the
                        # picture holds for a clip; the line is not dropped:
                        # the engine took its record back, and it joins the
                        # next plan.
                        carry_text = " ".join(x for x in (text, carry_text) if x)
                        timing.write("line_requeued", turn=state.turn_number, lines=list(plan_lines),
                                     reason="the Director call that answered them failed")
                        player.status("the Director could not answer just now; your line goes into the next beat")
                        planned_text = ""
                        return
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
            once, with one disposition."""
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

        fire_due: dict[int, float] = {}

        async def _listen(turn: int) -> None:
            """Wait for the just-in-time moment of segment `turn`, taking the
            viewer's lines as they come: a line cuts the beat short and
            has the Director write the next one at once."""
            fire_at = None
            while True:
                need = _needed_at()
                if need is None:
                    break  # building the lead: as fast as possible
                fire_at = need - _render_estimate() - config.buffer_seconds
                if abs(fire_due.get(turn, float("inf")) - fire_at) > FIRE_DUE_STEP_S:
                    # When this segment's render is due to fire (the record's wall
                    # time plus `in_s`): a scripted viewer can time a line against
                    # it; written again only if the moment moves.
                    fire_due[turn] = fire_at
                    timing.write("fire_due", turn=turn, in_s=round(fire_at - time.monotonic(), 4))
                if graph.has_next_clip():
                    wait_until = fire_at
                    player.status(f"listening for your input before segment {turn}")
                elif config.plan_early:
                    # Plan the next beat now; the loop then waits for
                    # the fire with the beat in hand, still listening.
                    player.status(f"the Director is writing segment {turn}")
                    await _plan(await _take())
                    continue
                else:
                    wait_until = fire_at - _director_lead()
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
                    # The previous clip's render is known before this
                    # clip is emitted (it lands about 1.2 s before this
                    # clip's fire in steady state, so nothing waits).
                    if not await _drain_prev():
                        if not config.fail_soft:
                            timing.write("render_skipped", turn=turn - 1)
                        else:
                            _roll_back(turn - 1, "its render failed")
                            _fail(turn - 1, "render failed")
                            turn -= 2          # replan the failed segment's number
                            continue
                    elif i2v is not None and turn > 1:
                        failures = 0
                    try:
                        state = await graph.emit(state)
                    except EmitFailed as exc:
                        timing.write("emit_failed", turn=turn, error=str(exc))
                        story_logger.log_note(f"Segment {turn} could not be emitted ({exc}); replanned.")
                        _fail(turn, "emit failed")
                        turn -= 1
                        continue
                    if i2v is None:
                        failures = 0
                    _log_emit(timing, graph, turn)
                    last_landed = None
                    if beat_lines:
                        timing.write("line_landed", turn=turn, lines=list(beat_lines))
                        last_landed = (turn, list(beat_lines))
                        beat_lines.clear()

                    if state.current_shot is not None:
                        item = _playable(state)
                        if i2v is None:
                            # ── Text-only film ──────────────────────────
                            # No renderer: the clip plays as text for its
                            # own duration.
                            await _enqueue(item)
                        else:
                            # ── The render path ─────────────────────────
                            # Wait for the previous render (the sliding
                            # window is sequential), then fire this one in
                            # the background.
                            player.status(f"rendering segment {turn}")
                            shape = state.current_shape
                            # How long until this clip is due on air (None
                            # while the lead builds): the render's budget.
                            need = _needed_at()
                            timing.write("fire", turn=turn, seconds=round(shape.delivered_seconds, 4),
                                         frames=shape.render_frames,
                                         due_in=round(need - time.monotonic(), 4) if need is not None else None)
                            _prev_render = asyncio.create_task(
                                _bg_render_and_enqueue(
                                    item,
                                    prompt=state.current_shot.i2v_prompt,
                                    seed_image=seed_image,
                                    shape=shape,
                                    clip_id=state.current_clip_id or None,
                                    parent_clip_id=state.current_parent_clip_id if state.current_clip_id else None,
                                )
                            )
                            _prev_hold = shape.delivered_seconds / player.time_scale

                    set_trace_output(_trace_output(state))

                _commit_history(state)
                _log_story_turn(story_logger, state)
            # A bounded run: let the last render land before signalling the end.
            if not await _drain_prev():
                timing.write("render_skipped", turn=turn, reason="the last render failed at the end of a bounded run")
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
            # Startup gate: wait until `startup_clips + 1` items are queued (or
            # the producer is already done). At 0 this is just "wait for the
            # first segment."
            while queue.qsize() < gate and not stop_event.is_set() and not producer_done.is_set():
                try:
                    await asyncio.wait_for(lead_ready.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass
            if stop_event.is_set():
                return
            if config.startup_clips > 0:
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
    beat, shot = state.current_beat, state.current_shot
    return Playable(
        turn=state.turn_number,
        clip_id=state.current_clip_id,
        duration=float(shot.duration_seconds) if shot else state.current_shape.delivered_seconds,
        user_input=state.user_input,
        narration=beat.narration if beat else "",
        dialogue=list(state.current_dialogue),
        on_screen=list(shot.on_screen) if shot else [],
        camera=shot.camera if shot else "",
        intervention=state.current_intervention,
        intervention_reason=state.current_intervention_reason,
        scene=state.context_brief,
        beat=state.current_beat_intent,
        clip=state.current_clip,
        speech=bool(state.current_dialogue),
    )
