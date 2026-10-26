"""The film loop.

`run_live` is a producer emitting and rendering clips just in time and a
`Player` (the web player) showing them while the viewer steers mid-stream.
It plays the viewer's story on from where they left it: every clip that
goes on air is committed to the story folder, and its file is deleted
once it has played, so nothing of the film is kept but the story and the
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

from src.agents.now import NowFailed
from src.engine import EmitFailed, Emitted, Engine
from src.i2v import LtxvI2V
from src.i2v.base import I2VBackend
from src.i2v.ltxv import cut_last_frames
from src.i2v.shape import DEFAULT_FPS, ClipShape
from src.models.config import Config
from src.models.frame_zero import FrameZero
from src.playback import Playable, Player
from src.story_folder import StoryFolder
from src.util.media import probe_duration
from src.util.transcript import Transcript
from src.util.timing_log import TimingLog, git_revision

# A failed render is retried once only if the retry can still finish
# before its clip is due (the p95 of the last RENDER_WINDOW renders plus
# JIT_MARGIN_S); this many failed clips in a row stop the film instead
# of looping on a renderer that is down, and this many failed saves in a
# row stop it rather than play on unsaved.
JIT_MARGIN_S = 0.5
RENDER_WINDOW = 20
MAX_CONSECUTIVE_FAILURES = 3
MAX_FAILED_SAVES = 3
# A `fire_due` record is written again only when the due moment moves by more than this.
FIRE_DUE_STEP_S = 0.2

logger = logging.getLogger(__name__)


def _make_film_id(config_name: str) -> str:
    """The film's id: the name of its folder under `logs/films/`."""
    return f"{config_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _start_timing(config: Config, frame_zero: FrameZero, film_id: str, film_dir: Path, *,
                  shape: ClipShape, **fields) -> TimingLog:
    timing = TimingLog(film_dir / "timing.jsonl")
    revision, dirty = git_revision()
    timing.write(
        "film_start", film=film_id, config=config.name, first_frame=frame_zero.scene,
        revision=revision, dirty=dirty,
        now_model=config.now_model,
        now_reasoning_effort=config.now_reasoning_effort or None,
        video=config.video_enabled,
        clip_seconds=round(shape.delivered_seconds, 4), shape=shape.record(),
        **fields,
    )
    return timing


def _input_fields(text: str) -> dict:
    """A viewer input as the timing log keeps it: its length and a short
    hash (the transcript has the words)."""
    return {"chars": len(text), "sha": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}


def _log_emit(timing: TimingLog, engine: Engine, clip_no: int) -> None:
    """The clip the engine just emitted, from its record."""
    last = engine.world.clips[-1] if engine.world.clips else None
    if last is None or last.clip != clip_no:
        return
    timing.write("emit", clip=clip_no, clip_id=last.clip_id, beat=last.beat, beat_clip=last.beat_clip,
                 of=last.beat_clips, seconds=last.duration, speech=bool(last.dialogue))


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
    here, not at the first render, and so does a Frame Zero without a
    reference: the renderer starts every story from one."""
    if i2v is None:
        return
    try:
        if not seed_image:
            raise ValueError("Frame Zero has no `reference_image`; the renderer starts "
                             "the story from one, so a video run needs it")
        verdict = i2v.check_reference(seed_image)
    except Exception as exc:
        timing.write("reference", image=seed_image, refused=str(exc))
        raise
    if verdict:
        timing.write("reference", image=seed_image, **verdict)


def _maybe_i2v(config: Config, clips_dir: Path, seed_key: str) -> I2VBackend | None:
    """The run's renderer: its clips go to `clips_dir` until they have
    played, and its noise seeds derive from `seed_key` (the story's id)."""
    if not config.video_enabled:
        return None
    return LtxvI2V(
        server_url=config.ltxv_server_url,
        continuity_strength=config.ltxv_continuity_strength,
        output_dir=clips_dir,
        seed_key=seed_key,
    )


async def run_live(
    *,
    config: Config,
    folder: StoryFolder,
    player: Player,
    log_dir: Path | str = "logs",
    max_clips: int | None = None,
) -> None:
    """Live loop: a producer generating clips, a player showing them.

    The producer emits one clip at a time from Now's current beat and
    pushes each rendered clip onto a bounded queue (size =
    `startup_clips + 1`). The consumer hands them to the player one at a
    time; the player holds each for its duration, so the server is the
    clock. `max_clips` stops the producer after that many clips;
    otherwise it runs until the film is stopped.

    Just in time: once playback runs, a clip's render is fired as late as
    it safely can be (the render estimate plus `buffer_seconds` before the
    clip is due on air), and Now is asked for the next beat as soon as
    the current one has no clip left. Until the fire the producer listens
    for the viewer. An input cuts the beat in progress short (its
    unrendered clips are dropped), cancels a Now call in flight, and Now
    writes the next beat from that input at once, so the input reaches the
    earliest clip that is not yet rendering. Before playback starts the
    producer runs flat out to build the lead.

    With a video backend each item is a rendered clip. Without one it is
    the clip's text, held for the clip's duration.

    A film that cannot go on (Now failed twice, clips or saves failed
    three times in a row) raises once it has stopped, saved as of the clip
    on air, so the page can say why; clips already rendered play out
    first, unless saving is what failed.

    The film's records go to `logs/films/<id>/`: the transcript, the
    models' raw outputs and the timing log (fires, renders, the queue, air
    time, viewer inputs, Now calls; see `src.util.timing_log`). The story
    itself is committed as each clip goes on air (`Engine.commit`), so a
    film stopped or killed anywhere resumes from the last clip the viewer
    saw.
    """
    frame_zero = folder.frame_zero
    film_id = _make_film_id(config.name)
    film_dir = Path(log_dir) / "films" / film_id
    transcript = Transcript(film_dir / "transcript.md", film_id=film_id, config_name=config.name,
                            first_frame=frame_zero.scene)

    queue_size = max(1, config.startup_clips + 1)
    clips_dir = film_dir / "clips"
    i2v = _maybe_i2v(config, clips_dir, folder.id)
    shape = ClipShape.from_config(config, fps=i2v.frame_rate if i2v is not None else DEFAULT_FPS)
    timing = _start_timing(
        config, frame_zero, film_id, film_dir, shape=shape,
        startup_clips=config.startup_clips, queue_size=queue_size,
        buffer_seconds=config.buffer_seconds, jit_margin=JIT_MARGIN_S,
    )
    _check_reference(i2v, frame_zero.reference_image, timing)
    _check_shape(i2v, shape, timing)
    engine = Engine(config=config, folder=folder, film_dir=film_dir, timing=timing, shape=shape)
    resumed_at = engine.world.clip
    if i2v is not None and resumed_at:
        if folder.last_frames is not None:
            i2v.resume_from(folder.last_frames)
        else:
            # Nothing saved to continue the picture from: it starts again
            # from the first frame, and Now hears so.
            engine.note_reseed(resumed_at + 1)
    timing.write("story", id=folder.id, resumed_at=resumed_at, last_frames=folder.last_frames is not None)
    print(("Your story continues from clip %d." % resumed_at if resumed_at else "Your story begins.")
          + f" It plays as {'video' if i2v is not None else 'text'}.", flush=True)

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
    # Why the film cannot go on, raised once it has stopped; and the saves
    # that failed in a row.
    failed: list[BaseException] = []
    saves = {"failed": 0}
    tasks: list[asyncio.Task] = []

    def _fail_now(exc: BaseException) -> None:
        """Stop the film at once: it cannot go on safely."""
        if not failed:
            failed.append(exc)
        stop_event.set()
        for task in tasks:
            task.cancel()

    async def _commit(item: Playable, before: asyncio.Task | None) -> None:
        """Save the story as of `item`, now on air: the world then and, for
        a clip, its last frames. Then the clips two before it, which no page
        needs any more, are deleted."""
        if before is not None:
            await asyncio.gather(before, return_exceptions=True)
        last_frames = None
        try:
            if item.path:
                last_frames = await asyncio.to_thread(cut_last_frames, item.path,
                                                      clips_dir / f"last_frames_{item.clip:06d}.mp4", fps=shape.fps)
        except Exception as exc:
            logger.warning("Could not cut the last frames of clip %s (%s); the story keeps the previous ones",
                           item.clip, exc)
            timing.write("last_frames_failed", clip=item.clip, error=repr(exc))
        try:
            await engine.commit(item.clip, last_frames)
            saves["failed"] = 0
        except Exception as exc:
            logger.exception("Could not save the story as of clip %s", item.clip)
            timing.write("commit_failed", clip=item.clip, error=repr(exc))
            saves["failed"] += 1
            if saves["failed"] >= MAX_FAILED_SAVES:
                _fail_now(RuntimeError(f"the story could not be saved {saves['failed']} times in a row "
                                       f"({type(exc).__name__}: {exc}); it is saved as of the last clip that was"))
        finally:
            if last_frames is not None:
                last_frames.unlink(missing_ok=True)
        if item.path:
            aired[item.clip] = item.path
        for n in [n for n in aired if n <= item.clip - 2]:
            Path(aired.pop(n)).unlink(missing_ok=True)
    # The player's clock as the producer sees it: which clip is on air,
    # when it started (monotonic) and how long it holds, plus the rendered
    # clips waiting behind it.
    air: dict = {"clip": None, "start": None, "hold": 0.0}
    queued_holds: list[float] = []
    # Viewer inputs as the timing log follows them, by the player's input
    # id: taken into a Now call, then carried by the planned beat until its
    # first clip is emitted (landed) or the beat is cut first (dropped).
    plan_inputs: list[int] = []
    beat_inputs: list[int] = []
    # The player's input id → the input's id in the viewer's memory.
    memory_ids: dict[int, int] = {}

    def _on_input(input_id: int, text: str) -> None:
        into = time.monotonic() - air["start"] if air["start"] is not None else None
        timing.write("input_received", input=input_id, on_air=air["clip"],
                     into=round(into, 4) if into is not None else None, **_input_fields(text))
        memory_ids[input_id] = engine.heard(text, air["clip"])

    player.on_input = _on_input

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
        timing.write("enqueued", clip=item.clip, duration=item.duration, queued=queue.qsize(),
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
                image_path=seed_image, prompt=prompt, clip_no=item.clip, shape=shape,
                clip_id=clip_id, parent_clip_id=parent_clip_id,
            )
            render_s = time.monotonic() - t0
            timing.write("render_done", clip=item.clip, ok=bool(video_path), render_s=round(render_s, 4),
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
            timing.write("render_retry", clip=item.clip, retry=retry, needs_s=round(needs, 4),
                         left_s=round(left, 4) if left is not None else None)
            if not retry:
                return False
        item.path = video_path
        # The clip's head repeats the previous clip's tail, over which the
        # page cuts and crossfades, unless the clip was seeded from the
        # reference (the story's first, or a server restart's), which
        # repeats nothing.
        if not i2v.last_render.get("reference"):
            item.overlap_seconds = shape.overlap_seconds
        if i2v.last_render.get("reseeded"):
            # A restart the client could not restore from the last clip's
            # tail: Now hears the picture was reset.
            timing.write("reseeded", clip=item.clip)
            engine.note_reseed(item.clip)
        item.duration = shape.delivered_seconds
        measured = await probe_duration(item.path)
        if measured is not None and abs(measured - shape.render_seconds) > 1.5 / shape.fps:
            timing.write("shape_mismatch", clip=item.clip, frames=shape.render_frames,
                         expected_s=round(shape.render_seconds, 4), measured_s=round(measured, 4))
        await _enqueue(item)
        return True

    async def producer() -> None:
        seed_image = frame_zero.reference_image
        clip_no = resumed_at
        # Background render task for pipelining, and the hold of the clip it
        # renders (the shape's delivered seconds on the player's clock).
        _prev_render: asyncio.Task | None = None
        _prev_hold = 0.0
        # The viewer text the last plan answered ("" when it answered none),
        # while its beat has not emitted a clip: an input arriving then is
        # joined to it.
        planned_text = ""
        # Viewer text whose answer was rolled back, for the next plan; the
        # inputs that landed on the last clip; failed clips in a row.
        carry_text = ""
        last_landed: tuple[int, list[int]] | None = None
        failures = 0

        def _fail(failed: int, reason: str) -> None:
            """Count a failed clip; stop the film after too many in a row."""
            nonlocal failures
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                timing.write("error", where="producer", clip=failed,
                             error=f"{failures} clips failed in a row ({reason}); stopping")
                raise RuntimeError(f"{failures} clips failed in a row: {reason}")

        def _roll_back(failed: int, reason: str) -> None:
            """The last emitted clip never reached the viewer: take it back
            and have the next plan start from the clip before."""
            nonlocal carry_text
            carry = engine.rollback_clip(failed, reason)
            requeued = list(last_landed[1]) if last_landed and last_landed[0] == failed else []
            requeued += beat_inputs
            beat_inputs.clear()
            if requeued:
                plan_inputs[:0] = requeued
                timing.write("input_requeued", clip=failed, inputs=requeued,
                             reason="the clip that answered them never reached the screen")
            if carry:
                carry_text = " ".join(x for x in (carry, carry_text) if x)
            timing.write("rollback", clip=failed, reason=reason)
            transcript.note(f"Clip {failed} never reached the viewer ({reason}); "
                            "the world was rolled back to before it and the clip replanned.")

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
            """Everything the viewer typed since the last call, after any
            input whose answer never reached the screen (a rolled-back
            clip). The inputs join the next plan."""
            nonlocal carry_text
            text = await player.next_input()
            if player.taken:
                plan_inputs.extend(player.taken)
                timing.write("input_taken", clip=clip_no, inputs=list(player.taken))
            if carry_text:
                text = " ".join(x for x in (carry_text, text) if x)
                carry_text = ""
            return text

        def _abort(reason: str) -> None:
            """Cut the current beat short; inputs its plan carried and no
            clip has shown yet are dropped with it."""
            engine.abort_beat(reason)
            if beat_inputs:
                timing.write("input_dropped", clip=clip_no, inputs=list(beat_inputs),
                             reason="the beat that carried them was cut before its first clip")
                beat_inputs.clear()

        async def _plan(text: str) -> None:
            """Ask Now for the next beat from `text`. An input that arrives
            while the call runs cancels it, and the call restarts with
            everything the viewer typed. A film stopped meanwhile cancels
            the call too: nothing keeps running, or billing, after it."""
            nonlocal planned_text
            while True:
                ids = [memory_ids[i] for i in plan_inputs if i in memory_ids]
                call = asyncio.create_task(engine.plan(clip_no, text, ids))
                waiter = asyncio.create_task(player.wait_input(None))
                try:
                    done, _ = await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    waiter.cancel()
                    if not call.done():
                        call.cancel()
                        await asyncio.gather(call, return_exceptions=True)
                if call in done:
                    call.result()
                    beat_inputs[:] = plan_inputs
                    plan_inputs.clear()
                    planned_text = text
                    return
                engine.discard_pending_input(clip_no)
                more = await _take()
                text = " ".join(x for x in (text, more) if x)

        async def _answer(text: str) -> None:
            """A viewer input arrived before the next clip was emitted: cut
            the beat in progress short and plan from the input. When the
            planned beat has not played a clip and its plan answered an
            earlier input (the second of the pair arrived after Now
            returned), that earlier input is joined to this one and planned
            once, as an input that cancels a call in flight is: its
            recorded input is dropped so the pair is recorded once, with
            one outcome."""
            if engine.beat_unplayed() and planned_text:
                engine.discard_pending_input(clip_no)
                plan_inputs[:0] = beat_inputs     # the earlier input travels with this one
                beat_inputs.clear()
                text = f"{planned_text} {text}"
            if engine.has_next_clip():
                _abort("the viewer typed")
            await _plan(text)

        fire_due: dict[int, float] = {}

        async def _listen(n: int) -> None:
            """Wait for the just-in-time moment of clip `n`, taking the
            viewer's inputs as they come: an input cuts the beat short and
            has Now write the next one at once."""
            while True:
                need = _needed_at()
                if need is None:
                    break  # building the lead: as fast as possible
                fire_at = need - _render_estimate() - config.buffer_seconds
                if abs(fire_due.get(n, float("inf")) - fire_at) > FIRE_DUE_STEP_S:
                    # When this clip's render is due to fire (the record's
                    # wall time plus `in_s`); written again only if the
                    # moment moves.
                    fire_due[n] = fire_at
                    timing.write("fire_due", clip=n, in_s=round(fire_at - time.monotonic(), 4))
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
            await engine.open()
            while not stop_event.is_set():
                if max_clips is not None and clip_no - resumed_at >= max_clips:
                    break
                clip_no += 1

                await _listen(clip_no)
                # The previous clip's render is known before this clip is
                # emitted (it lands about 1.2 s before this clip's fire in
                # steady state, so nothing waits).
                if not await _drain_prev():
                    _roll_back(clip_no - 1, "its render failed")
                    _fail(clip_no - 1, "render failed")
                    clip_no -= 2          # replan the failed clip's number
                    continue
                elif i2v is not None and clip_no > 1:
                    failures = 0
                try:
                    emitted = await engine.emit(clip_no)
                except EmitFailed as exc:
                    timing.write("emit_failed", clip=clip_no, error=str(exc))
                    transcript.note(f"Clip {clip_no} could not be emitted ({exc}); replanned.")
                    _fail(clip_no, "emit failed")
                    clip_no -= 1
                    continue
                if i2v is None:
                    failures = 0
                _log_emit(timing, engine, clip_no)
                last_landed = None
                if beat_inputs:
                    timing.write("input_landed", clip=clip_no, inputs=list(beat_inputs))
                    last_landed = (clip_no, list(beat_inputs))
                    beat_inputs.clear()

                item = _playable(emitted)
                if i2v is None:
                    # No renderer: the clip plays as text for its own duration.
                    await _enqueue(item)
                else:
                    # The previous render was waited for above (the sliding
                    # window is sequential); fire this one in the
                    # background. Its budget is how long until the clip is
                    # due on air (None while the lead builds).
                    need = _needed_at()
                    timing.write("fire", clip=clip_no, seconds=round(shape.delivered_seconds, 4),
                                 frames=shape.render_frames,
                                 due_in=round(need - time.monotonic(), 4) if need is not None else None)
                    _prev_render = asyncio.create_task(
                        _bg_render_and_enqueue(
                            item,
                            prompt=emitted.prompt,
                            seed_image=seed_image,
                            clip_id=emitted.clip_id or None,
                            parent_clip_id=emitted.parent_clip_id if emitted.clip_id else None,
                        )
                    )
                    _prev_hold = shape.delivered_seconds

                transcript.clip(emitted)
            # A bounded run: let the last render land before signalling the end.
            if not await _drain_prev():
                timing.write("render_skipped", clip=clip_no, reason="the last render failed at the end of a bounded run")
        except asyncio.CancelledError:
            if _prev_render is not None:
                _prev_render.cancel()
            raise
        except Exception as exc:
            # The film ends once what is rendered has played; the page is
            # told why.
            if isinstance(exc, NowFailed):
                logger.error("Clip %s: %s", clip_no, exc)
            else:
                logger.exception("Live producer crashed on clip %s", clip_no)
            timing.write("error", where="producer", clip=clip_no, error=repr(exc))
            transcript.note(f"The film stopped at clip {clip_no}: {exc}")
            if not failed:
                failed.append(exc)
            await _drain_prev()   # the clip rendering now still plays
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
            # first clip."
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
                air["clip"] = item.clip
                air["start"] = time.monotonic()
                air["hold"] = _hold_of(item)
                commit_task = asyncio.create_task(_commit(item, commit_task))
                timing.write("playing", clip=item.clip, duration=item.duration,
                             hold=round(air["hold"], 4), waited=round(waited, 4), first=not played)
                played.append(item)
                await player.play(item)
                timing.write("hold_end", clip=item.clip)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Live consumer crashed")
            timing.write("error", where="consumer", error=repr(exc))
            _fail_now(exc)

    await player.start()
    producer_task = asyncio.create_task(producer())
    consumer_task = asyncio.create_task(consumer())
    tasks += [producer_task, consumer_task]

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
        timing.write("film_end", clip=played[-1].clip if played else resumed_at, played=len(played),
                     stopped_by=repr(failed[0]) if failed else None)
        timing.close()
    if failed:
        raise failed[0]


def _playable(emitted: Emitted) -> Playable:
    """The viewer-facing view of the clip just emitted. `path` and the
    real `duration` are filled in once a rendered clip exists."""
    return Playable(
        clip=emitted.clip,
        duration=emitted.duration,
        action=emitted.action,
        dialogue=list(emitted.dialogue),
        speech=bool(emitted.dialogue),
    )
