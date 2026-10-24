"""The film loop.

`run_live` is a producer emitting and rendering clips just in time and a
`Player` (the web player) showing them while the viewer steers mid-stream.
It plays one adventure on from where the viewer left it: every clip that
goes on air is committed to the adventure, and its file is deleted once
it has played, so nothing of the film is kept but the story and the
frames that continue it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from src.adventure import Adventure
from src.engine import NowEngine, EmitFailed
from src.i2v import LtxvI2V
from src.i2v.base import I2VBackend
from src.i2v.ltxv import cut_window
from src.i2v.shape import DEFAULT_FPS, ClipShape
from src.models.config import Config
from src.models.story import FrameZero
from src.playback import Playable, Player
from src.state.story_state import HistoryEntry, StoryState
from src.util.media import probe_duration
from src.util.story_log import StoryLogger
from src.util.timing_log import TimingLog, git_revision

# A failed render is retried once only if the retry can still finish
# before its clip is due (the p95 of the last RENDER_WINDOW renders plus
# JIT_MARGIN_S); this many failed segments in a row stop the film instead
# of looping on a renderer that is down.
JIT_MARGIN_S = 0.5
RENDER_WINDOW = 20
MAX_CONSECUTIVE_FAILURES = 3
# A `fire_due` record is written again only when the due moment moves by more than this.
FIRE_DUE_STEP_S = 0.2

logger = logging.getLogger(__name__)


def _make_session_id(config_name: str) -> str:
    """The run's id: its transcript's name and its session directory."""
    return f"{config_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _start_timing(config: Config, story: FrameZero, session_id: str, session_dir: Path, *,
                  shape: ClipShape, **fields) -> TimingLog:
    timing = TimingLog(session_dir / "timing.jsonl")
    revision, dirty = git_revision()
    timing.write(
        "session_start", session=session_id, config=config.name, first_frame=story.scene,
        revision=revision, dirty=dirty,
        now_model=config.now_model,
        now_reasoning_effort=config.now_reasoning_effort or None,
        video=config.video_enabled,
        clip_seconds=round(shape.delivered_seconds, 4), shape=shape.record(),
        **fields,
    )
    return timing


def _line_fields(text: str) -> dict:
    """A viewer line as the timing log keeps it: its length and a short
    hash (the transcript has the words)."""
    return {"chars": len(text), "sha": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}


def _log_emit(timing: TimingLog, engine: NowEngine, turn: int) -> None:
    """The clip the engine just emitted, from its event record."""
    ev = engine.world.events[-1] if engine.world is not None and engine.world.events else None
    if ev is None or ev.segment != turn:
        return
    timing.write("emit", turn=turn, clip_id=ev.clip_id, beat=ev.beat, clip=ev.clip_index, of=ev.clip_count,
                 seconds=ev.duration, speech=bool(ev.dialogue))


def _check_shape(i2v: I2VBackend | None, shape: ClipShape, timing: TimingLog) -> None:
    """Refuse the run before anything renders when the renderer has not
    warmed the run's shape; the verdict goes to the log."""
    if i2v is None:
        return
    try:
        verdict = i2v.check_shape(shape)
    except Exception as exc:
        timing.write("shapes", frames=shape.render_frames, refused=str(exc))
        raise
    if verdict:
        timing.write("shapes", **verdict)


def _check_reference(i2v: I2VBackend | None, seed_image: str, timing: TimingLog) -> None:
    """Validate the reference against the render frame before anything
    renders; the verdict goes to the timing log. A refusal stops the run
    here, not at the first render, and so does a story without a
    reference: the renderer starts every film from one."""
    if i2v is None:
        return
    try:
        if not seed_image:
            raise ValueError("the frame zero has no `reference_image`; the renderer starts "
                             "the film from one, so a video run needs it")
        verdict = i2v.check_reference(seed_image)
    except Exception as exc:
        timing.write("reference", image=seed_image, refused=str(exc))
        raise
    if verdict:
        timing.write("reference", image=seed_image, **verdict)


def _maybe_i2v(config: Config, clips_dir: Path, seed_key: str) -> I2VBackend | None:
    """The run's renderer: its clips go to `clips_dir` until they have
    played, and its seeds derive from `seed_key` (the adventure, and the
    timeline of a branch)."""
    if not config.video_enabled:
        return None
    return LtxvI2V(
        server_url=config.ltxv_server_url,
        continuity_strength=config.ltxv_continuity_strength,
        output_dir=clips_dir,
        seed_key=seed_key,
    )


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
        extra=state.turn_notes,
    )


async def run_live(
    *,
    config: Config,
    adventure: Adventure,
    player: Player,
    log_dir: Path | str = "logs",
    max_segments: int | None = None,
) -> StoryState:
    """Live loop: a producer generating clips, a player showing them.

    The producer emits one clip per segment from Now's current beat and
    pushes each rendered clip onto a bounded queue (size =
    `startup_clips + 1`). The consumer hands them to the player one at a
    time; the player holds each for its duration, so the server is the
    clock. `max_segments` stops the producer after that many clips;
    otherwise it runs until Ctrl+C.

    Just in time: once playback runs, a clip's render is fired as late as
    it safely can be (the render estimate plus `buffer_seconds` before the
    clip is due on air), and Now is asked for the next beat as soon as
    the current one has no clip left. Until the fire the producer listens
    for the viewer. A line cuts the beat in progress short (its unrendered
    clips are dropped), cancels a Now call in flight, and Now writes
    the next beat from that line at once, so the line reaches the earliest
    clip that is not yet rendering. Before playback starts the producer
    runs flat out to build the lead.

    With a video backend each item is a rendered clip. Without one it is
    the clip's text, held for the clip's duration.

    The run's timing (fires, renders, the queue, air time, viewer lines,
    Now calls) goes to `logs/sessions/<id>/timing.jsonl`; see
    `src.util.timing_log`. The adventure itself is committed as each clip
    goes on air (`NowEngine.commit`), so a run stopped or killed anywhere
    resumes from the last clip the viewer saw.
    """
    story: FrameZero = adventure.origin
    state = StoryState.initialize(story, config_name=config.name)
    session_id = _make_session_id(config.name)
    story_logger = StoryLogger(
        session_id=session_id,
        config_name=config.name,
        first_frame=story.scene,
        log_dir=log_dir,
    )

    queue_size = max(1, config.startup_clips + 1)
    session_dir = Path(log_dir) / "sessions" / session_id
    clips_dir = session_dir / "clips"
    i2v = _maybe_i2v(config, clips_dir, adventure.seed_key)
    shape = ClipShape.from_config(config, fps=i2v.frame_rate if i2v is not None else DEFAULT_FPS)
    timing = _start_timing(
        config, story, session_id, session_dir, shape=shape,
        startup_clips=config.startup_clips, queue_size=queue_size,
        buffer_seconds=config.buffer_seconds, jit_margin=JIT_MARGIN_S,
    )
    _check_reference(i2v, story.reference_image, timing)
    _check_shape(i2v, shape, timing)
    engine = NowEngine(config=config, adventure=adventure, session_dir=session_dir, timing=timing, shape=shape)
    resumed_at = engine.world.segment
    if i2v is not None and resumed_at:
        if adventure.window is not None:
            i2v.resume_from(adventure.window)
        else:
            # Nothing saved to continue the picture from: it starts again
            # from the first frame, and Now hears so.
            engine.note_reseed(resumed_at + 1)
    timing.write("adventure", id=adventure.id, timeline=adventure.manifest.timeline, resumed_at=resumed_at,
                 window=adventure.window is not None)
    print(("Your adventure continues from segment %d." % resumed_at if resumed_at else "Your adventure begins.")
          + f" It plays as {'video' if i2v is not None else 'text'}. Ctrl+C stops it.", flush=True)

    queue: asyncio.Queue[Playable | None] = asyncio.Queue(maxsize=queue_size)
    stop_event = asyncio.Event()
    producer_done = asyncio.Event()
    lead_ready = asyncio.Event()   # the lead is built: playback may start
    played: list[Playable] = []
    # Commits run one after another, each clip as it goes on air (each waits
    # for the one before, so the latest stands for all); the files of clips
    # that have played are deleted once the next is saved.
    commit_task: asyncio.Task | None = None
    aired: dict[int, str] = {}

    async def _commit(item: Playable, before: asyncio.Task | None) -> None:
        """Save the adventure as of `item`, now on air: the world then and,
        for a clip, its last frames. Then the clips two before it, which no
        page needs any more, are deleted."""
        if before is not None:
            await asyncio.gather(before, return_exceptions=True)
        window = None
        try:
            if item.path:
                window = await asyncio.to_thread(cut_window, item.path, clips_dir / f"window_{item.turn:06d}.mp4",
                                                 fps=shape.fps)
        except Exception as exc:
            logger.warning("Could not cut the last frames of segment %s (%s); the adventure keeps the previous ones",
                           item.turn, exc)
            timing.write("window_failed", turn=item.turn, error=repr(exc))
        try:
            await engine.commit(item.turn, window)
        except Exception as exc:
            logger.exception("Could not save the adventure as of segment %s", item.turn)
            timing.write("commit_failed", turn=item.turn, error=repr(exc))
        finally:
            if window is not None:
                window.unlink(missing_ok=True)
        if item.path:
            aired[item.turn] = item.path
        for turn in [t for t in aired if t <= item.turn - 2]:
            Path(aired.pop(turn)).unlink(missing_ok=True)
    # The player's clock as the producer sees it: which clip is on air,
    # when it started (monotonic) and how long it holds, plus the rendered
    # clips waiting behind it.
    air: dict = {"turn": None, "start": None, "hold": 0.0}
    queued_holds: list[float] = []
    # Viewer lines as the timing log follows them, by the player's line id:
    # taken into a Now call, then carried by the planned beat until
    # its first clip is emitted (landed) or the beat is cut first (dropped).
    plan_lines: list[int] = []
    beat_lines: list[int] = []
    # The player's line id → the line's id in the viewer's memory.
    said_ids: dict[int, int] = {}

    def _on_line(line_id: int, text: str) -> None:
        into = time.monotonic() - air["start"] if air["start"] is not None else None
        timing.write("line_received", line=line_id, on_air=air["turn"],
                     into=round(into, 4) if into is not None else None, **_line_fields(text))
        said_ids[line_id] = engine.heard(text, air["turn"])

    player.on_line = _on_line

    def _hold_of(item: Playable) -> float:
        return float(item.duration)

    # A running average of the measured render time, for the just-in-time
    # schedule. None until the first measurement.
    est: dict[str, float | None] = {"render": None}

    def _learn_render(seconds: float) -> None:
        prev = est["render"]
        est["render"] = seconds if prev is None else 0.5 * prev + 0.5 * seconds

    def _render_estimate() -> float:
        return est["render"] or 0.0

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
        item: Playable, *, prompt: str, seed_image: str,
        clip_id: int | None = None, parent_clip_id: int | None = None,
    ) -> bool:
        """Render one clip in the background and enqueue it. The player
        holds it for the shape's delivered frames; the file's measured
        length is only checked against the shape. A failed render is
        retried once when the retry can still land before the clip is due.
        Returns whether the clip was queued."""
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
                _learn_render(render_s)
                renders.append(render_s)
                del renders[:-RENDER_WINDOW]
                break
            if attempt == 2:
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
        item.path = video_path
        # The clip's head repeats the previous clip's tail, over which the
        # page cuts and crossfades, unless the clip was seeded from the
        # reference (the run's first, or a server restart's), which repeats
        # nothing.
        if not i2v.last_render.get("reference"):
            item.overlap_seconds = shape.overlap_seconds
        if i2v.last_render.get("reseeded"):
            # A restart the client could not restore from the last clip's
            # tail: Now hears the picture was reset.
            timing.write("reseeded", turn=item.turn)
            engine.note_reseed(item.turn)
        item.duration = shape.delivered_seconds
        measured = await probe_duration(item.path)
        if measured is not None and abs(measured - shape.render_seconds) > 1.5 / shape.fps:
            timing.write("shape_mismatch", turn=item.turn, frames=shape.render_frames,
                         expected_s=round(shape.render_seconds, 4), measured_s=round(measured, 4))
        await _enqueue(item)
        return True

    async def producer() -> None:
        nonlocal state
        seed_image = story.reference_image
        turn = resumed_at
        # Background render task for pipelining, and the hold of the clip it
        # renders (the shape's delivered seconds on the player's clock).
        _prev_render: asyncio.Task | None = None
        _prev_hold = 0.0
        # The viewer text the last plan answered ("" when it answered none),
        # while its beat has not emitted a clip: a line arriving then is
        # joined to it.
        planned_text = ""
        # Viewer text whose answer was rolled back, for the next plan; the
        # lines that landed on the last clip; failed segments in a row.
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
                raise RuntimeError(f"{failures} segments failed in a row: {reason}")

        def _roll_back(turn_failed: int, reason: str) -> None:
            """The last emitted clip never reached the viewer: take it back
            and have the next plan start from the clip before."""
            nonlocal carry_text
            carry = engine.rollback_clip(turn_failed, reason)
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

        def _abort(reason: str) -> None:
            """Cut the current beat short; lines its plan carried and no clip
            has shown yet are dropped with it."""
            engine.abort_beat(reason)
            if beat_lines:
                timing.write("line_dropped", turn=state.turn_number, lines=list(beat_lines),
                             reason="the beat that carried them was cut before its first clip")
                beat_lines.clear()

        async def _plan(text: str) -> None:
            """Ask Now for the next beat from `text`. A line that
            arrives while the call runs cancels it, and the call restarts
            with everything the viewer typed."""
            nonlocal state, planned_text, carry_text
            while True:
                state.user_input = text
                state.user_said = [said_ids[i] for i in plan_lines if i in said_ids]
                call = asyncio.create_task(engine.plan(state))
                waiter = asyncio.create_task(player.wait_input(None))
                done, _ = await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
                if call in done:
                    waiter.cancel()
                    await call
                    if engine.last_plan_fallback:
                        # The call answering the line failed and the
                        # picture holds for a clip; the line is not dropped:
                        # the engine took its record back, and it joins the
                        # next plan.
                        carry_text = " ".join(x for x in (text, carry_text) if x)
                        timing.write("line_requeued", turn=state.turn_number, lines=list(plan_lines),
                                     reason="the Now call that answered them failed")
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
                engine.discard_pending_input(state.turn_number)
                more = await _take()
                text = " ".join(x for x in (text, more) if x)

        async def _answer(text: str) -> None:
            """A viewer line arrived before the next segment was emitted: cut
            the beat in progress short and plan from the line. When the
            planned beat has not played a clip and its plan answered an
            earlier line (the second line of the pair arrived after the
            Now returned), that earlier line is joined to this one
            and planned once, as a line that cancels a call in flight is:
            its recorded intervention is dropped so the pair is recorded
            once, with one disposition."""
            if engine.beat_unplayed() and planned_text:
                engine.discard_pending_input(state.turn_number)
                plan_lines[:0] = beat_lines     # the earlier line travels with this one
                beat_lines.clear()
                text = f"{planned_text} {text}"
            if engine.has_next_clip():
                _abort("the viewer spoke")
            await _plan(text)

        fire_due: dict[int, float] = {}

        async def _listen(turn: int) -> None:
            """Wait for the just-in-time moment of segment `turn`, taking the
            viewer's lines as they come: a line cuts the beat short and
            has Now write the next one at once."""
            while True:
                need = _needed_at()
                if need is None:
                    break  # building the lead: as fast as possible
                fire_at = need - _render_estimate() - config.buffer_seconds
                if abs(fire_due.get(turn, float("inf")) - fire_at) > FIRE_DUE_STEP_S:
                    # When this segment's render is due to fire (the record's wall
                    # time plus `in_s`); written again only if the moment moves.
                    fire_due[turn] = fire_at
                    timing.write("fire_due", turn=turn, in_s=round(fire_at - time.monotonic(), 4))
                if not engine.has_next_clip():
                    # Plan the next beat now; the loop then waits for the
                    # fire with the beat in hand, still listening.
                    await _plan(await _take())
                    continue
                timeout = fire_at - time.monotonic()
                if timeout <= 0:
                    break
                if not await player.wait_input(timeout):
                    break  # time is up
                text = await _take()
                if text:
                    await _answer(text)
            # The moment has come. Anything typed since is taken now.
            if not engine.has_next_clip():
                await _plan(await _take())
            elif player.has_input():
                text = await _take()
                if text:
                    await _answer(text)

        try:
            while not stop_event.is_set():
                if max_segments is not None and turn - resumed_at >= max_segments:
                    break
                turn += 1
                state.turn_number = turn

                await _listen(turn)
                # The previous clip's render is known before this clip is
                # emitted (it lands about 1.2 s before this clip's fire in
                # steady state, so nothing waits).
                if not await _drain_prev():
                    _roll_back(turn - 1, "its render failed")
                    _fail(turn - 1, "render failed")
                    turn -= 2          # replan the failed segment's number
                    continue
                elif i2v is not None and turn > 1:
                    failures = 0
                try:
                    state = await engine.emit(state)
                except EmitFailed as exc:
                    timing.write("emit_failed", turn=turn, error=str(exc))
                    story_logger.log_note(f"Segment {turn} could not be emitted ({exc}); replanned.")
                    _fail(turn, "emit failed")
                    turn -= 1
                    continue
                if i2v is None:
                    failures = 0
                _log_emit(timing, engine, turn)
                last_landed = None
                if beat_lines:
                    timing.write("line_landed", turn=turn, lines=list(beat_lines))
                    last_landed = (turn, list(beat_lines))
                    beat_lines.clear()

                if state.current_shot is not None:
                    item = _playable(state)
                    if i2v is None:
                        # No renderer: the clip plays as text for its own duration.
                        await _enqueue(item)
                    else:
                        # The previous render was waited for above (the
                        # sliding window is sequential); fire this one in
                        # the background. Its budget is how long until the
                        # clip is due on air (None while the lead builds).
                        need = _needed_at()
                        timing.write("fire", turn=turn, seconds=round(shape.delivered_seconds, 4),
                                     frames=shape.render_frames,
                                     due_in=round(need - time.monotonic(), 4) if need is not None else None)
                        _prev_render = asyncio.create_task(
                            _bg_render_and_enqueue(
                                item,
                                prompt=state.current_shot.i2v_prompt,
                                seed_image=seed_image,
                                clip_id=state.current_clip_id or None,
                                parent_clip_id=state.current_parent_clip_id if state.current_clip_id else None,
                            )
                        )
                        _prev_hold = shape.delivered_seconds

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
        nonlocal commit_task
        try:
            # Startup gate: wait until `startup_clips + 1` items are queued (or
            # the producer is already done). At 0 this is just "wait for the
            # first segment."
            while queue.qsize() < queue_size and not stop_event.is_set() and not producer_done.is_set():
                try:
                    await asyncio.wait_for(lead_ready.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass
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
                # The player holds the item for its duration; when the next
                # one is not ready yet it keeps showing the last frame.
                air["turn"] = item.turn
                air["start"] = time.monotonic()
                air["hold"] = _hold_of(item)
                commit_task = asyncio.create_task(_commit(item, commit_task))
                timing.write("playing", turn=item.turn, duration=item.duration,
                             hold=round(air["hold"], 4), waited=round(waited, 4), first=not played)
                played.append(item)
                await player.play(item)
                timing.write("hold_end", turn=item.turn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Live consumer crashed")
            timing.write("error", where="consumer", error=repr(exc))
            stop_event.set()

    await player.start(session_id=session_id)
    producer_task = asyncio.create_task(producer())
    consumer_task = asyncio.create_task(consumer())

    try:
        await asyncio.gather(producer_task, consumer_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n(the film stopped)")
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
        # The last clip on air is saved before the run ends; the clips and
        # frames the run rendered are not kept.
        if commit_task is not None:
            await asyncio.gather(commit_task, return_exceptions=True)
        engine.close()
        shutil.rmtree(clips_dir, ignore_errors=True)
        timing.write("session_end", turn=state.turn_number, played=len(played))
        timing.close()
    return state


def _playable(state: StoryState) -> Playable:
    """The viewer-facing view of the segment just produced. `path` and the
    real `duration` are filled in once a clip exists."""
    beat, shot = state.current_beat, state.current_shot
    return Playable(
        turn=state.turn_number,
        duration=float(shot.duration_seconds) if shot else state.current_shape.delivered_seconds,
        narration=beat.narration if beat else "",
        dialogue=list(state.current_dialogue),
        speech=bool(state.current_dialogue),
    )
