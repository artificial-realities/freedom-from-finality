"""Director engine (`graph: "director"`).

Two LLM roles on two clocks, everything else deterministic:

- **Director** — one structured call per **beat**: one action or state
  of the film that lasts one to a few clips. Reads the assembled context
  and returns a `BeatPlan`; the engine then emits its clips one per
  segment with no LLM on the way, applying each clip's world changes as
  it goes. The Director is called again when the beat has played out, or
  at once when the viewer speaks: the rest of the beat is dropped and the
  next call is told where the picture actually ends.
- **Showrunner** — an async task on a slow clock, triggered at a plan
  (bootstrap, scene exit, stagnation, a replan flag, or every
  `compaction_interval` segments) and spawned when the beat's first clip
  has been applied, so its snapshot and its compaction window include
  the clip that caused it. At most one is in flight and runs are at
  least `showrunner_min_gap` segments apart; a replan flag that has to
  wait is remembered until a run takes it. Its update lands at the start
  of the first plan that finds it finished; nothing ever waits for it.
  Compacts the event log and writes the plan.

Code owns the rest: the typed `WorldModel`, validation and apply, the
prompt compiler, the pacing gates, and persistence per clip.

For the runner the engine keeps the legacy `StoryState` surface: it
fills `current_beat`, `current_shot`, `current_commentary` as views of
the clip, so rendering, playback, history and the transcript work
unchanged. `ainvoke` is the one-call form (plan if needed, then emit)
for the terminal loop and tests; the live loop calls `plan`, `emit` and
`abort_beat` itself so it can render just in time and interrupt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from src.agents import director as director_agent
from src.i2v.shape import ClipShape, ShapeRegistry, seconds_label
from src.agents import showrunner as showrunner_agent
from src.llm import build_backend
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Beat, Commentary, Shot
from src.state.story_state import StoryState
from src.story import compiler_prose
from src.story.compiler import compile_clip
from src.story.pacing import (
    SHOWRUNNER_REPLAN_AT,
    pacing_note,
    scene_note,
    tick_story_clock,
    voiceover_gate,
)
from src.story.schemas import BeatPlan, ClipSpec
from src.story.validator import Report, apply_clip, apply_showrunner
from src.story.world import EventRecord, WorldModel
from src.tts.elevenlabs import ElevenLabsTTS
from src.util.langfuse_setup import event as langfuse_event
from src.util.media import probe_duration
from src.util.timing_log import TimingLog

logger = logging.getLogger(__name__)


class EmitFailed(RuntimeError):
    """Emitting a clip raised; the engine restored the world and cut the beat
    (RUN-09), and the loop replans the segment."""


class DirectorEngine:
    def __init__(
        self,
        *,
        llm: LLMBackend,
        config: Config,
        tts: ElevenLabsTTS | None = None,
        session_dir: Path | str | None = None,
        timing: TimingLog | None = None,
        shapes: ShapeRegistry | None = None,
    ) -> None:
        self.llm = llm
        self.config = config
        # The run's clip shapes (RUN-01): each beat's clips get the selected
        # shape; nothing here derives a length from seconds.
        self.shapes = shapes or ShapeRegistry.from_config(config)
        if config.compiler not in ("labels", "prose"):
            raise ValueError(f"compiler must be 'labels' or 'prose', not {config.compiler!r}")
        self._beat_shape: ClipShape = self.shapes.default
        self.tts = tts
        self.session_dir = Path(session_dir) if session_dir else None
        # The run's timing log: each plan writes director_start/director_end.
        self.timing = timing or TimingLog(None)
        self.world: WorldModel | None = None
        self.llm_director = (
            build_backend(config.llm_backend, config.director_model or config.model,
                          service_tier=config.director_service_tier)
            if (config.director_model or config.director_service_tier) else llm
        )
        self.llm_showrunner = (
            build_backend(config.llm_backend, config.showrunner_model)
            if config.showrunner_model else llm
        )
        self._showrunner_task: asyncio.Task | None = None
        self._showrunner_meta: tuple[int, bool, str] | None = None  # (at_segment, force_replan, reason)
        self._showrunner_notes: list[str] = []
        self._last_spawn: int = 0   # segment the last Showrunner run was spawned at
        # A replan the Director asked for (scene_done / needs_replan) that no
        # run has taken yet: (reason, mode, segment first asked). `_trigger`
        # raises it again at every plan until a replan run spawns or a
        # replan lands (ARCH-02).
        self._held_trigger: tuple[str, str, int] | None = None
        # The trigger this plan decided on, spawned at the beat's first clip
        # after it is applied (ARCH-03): (reason, mode).
        self._pending_spawn: tuple[str, str] | None = None
        # Fail-soft (RUN-09): the engine and world as they were before the
        # last emitted clip, its segment, and the Showrunner updates landed
        # since (a plan for the next clip may land one before the clip's
        # render is known to have failed).
        self._emit_snapshot: dict | None = None
        self._landed_since: list[tuple] = []
        # The beat being emitted, clip by clip.
        self._beat: BeatPlan | None = None
        self._clip_index: int = 0
        self._beat_no: int = 0
        self._beat_input: str = ""          # the viewer line the beat answered
        self._plan_notes: list[str] = []    # transcript notes from the plan, shown on its first clip
        self._plan_meta: dict = {}          # vo_open, pacing, trigger, forced for the transcript
        self._last_end_state: str = ""      # the image the last emitted clip ends on
        self._last_beat: tuple[str, int, int] | None = None   # (intent, clips emitted, clips planned)
        self._pending_note: str = ""        # how the previous beat ended, for the next call

    # ── The one-call form ───────────────────────────────────────────

    async def ainvoke(self, state: StoryState) -> StoryState:
        """Plan a beat if none is in progress (a viewer line cuts a beat in
        progress short), then emit its next clip."""
        text = (state.user_input or "").strip()
        if text and self.has_next_clip():
            self.abort_beat("the viewer spoke")
        if not self.has_next_clip():
            await self.plan(state)
        return await self.emit(state)

    def has_next_clip(self) -> bool:
        return self._beat is not None and self._clip_index < len(self._beat.clips)

    def beat_unplayed(self) -> bool:
        """A beat is planned and none of its clips has been emitted yet."""
        return self._beat is not None and self._clip_index == 0

    # ── Plan: the Director call ─────────────────────────────────────

    async def plan(self, state: StoryState) -> None:
        """Ask the Director for the next beat, starting at `state.turn_number`
        and answering `state.user_input`. Cancellable while the LLM runs:
        the world is untouched until the call returns."""
        seg = state.turn_number
        answers = bool((state.user_input or "").strip())
        started = time.monotonic()
        self.timing.write("director_start", turn=seg, input=answers)
        try:
            cost = await self._plan(state)
        except BaseException as exc:
            cancelled = isinstance(exc, asyncio.CancelledError)
            self.timing.write("director_end", turn=seg, input=answers, cancelled=cancelled,
                              plan_s=round(time.monotonic() - started, 4),
                              **({} if cancelled else {"error": type(exc).__name__}))
            raise
        self.timing.write("director_end", turn=seg, input=answers,
                          plan_s=round(time.monotonic() - started, 4), **cost)

    async def _plan(self, state: StoryState) -> dict:
        """The plan itself. Returns what it cost, for the timing log: the
        beat number, its clip count, the Director call(s) alone
        (`director_s`, inside the whole plan's `plan_s`; `retry` = the
        validator re-ask ran), whether it fell back to holding the picture,
        and whether the bootstrap ran first."""
        if self.world is None:
            self.world = self._init_world(state)
            self._last_spawn = self.world.last_showrunner
        world = self.world
        seg = state.turn_number

        self._land_if_done(seg)
        notes: list[str] = list(self._showrunner_notes)
        self._showrunner_notes = []

        world.segment = seg
        text = (state.user_input or "").strip()
        if text:
            world.add_intervention(seg, text)

        bootstrap = world.plan.scene is None
        if bootstrap:
            await self._run_showrunner_now(state, reason="bootstrap: opening the film", mode="bootstrap", at_segment=seg - 1)
            notes.extend(self._showrunner_notes)
            self._showrunner_notes = []

        vo_open = voiceover_gate(state)
        pnote = pacing_note(world, self.config)
        snote = scene_note(world, self.config)

        # The shape is selected before the Director plans (RUN-01); every
        # clip of this beat is that long. The system prompt states the
        # default shape's length, so it stays byte-identical.
        shape = self.shapes.select()
        called = time.monotonic()
        beat, dnotes, retried = await director_agent.run(
            world=world, state=state, llm=self.llm_director, config=self.config,
            voiceover_open=vo_open, pacing_note=pnote, scene_note=snote,
            beat_status=self._beat_status(), clip_seconds=self.shapes.default.delivered_seconds,
        )
        director_s = time.monotonic() - called
        notes.extend(dnotes)
        fallback = beat is None
        if beat is None:
            beat = self._fallback_beat()
            notes.append("director: call failed; holding the picture for one clip")
        beat.clips = beat.clips[: director_agent.clip_cap(self.config, text)]
        if not vo_open:
            beat.voiceover = ""

        forced_done = False
        if not beat.scene_done and world.scene_seconds() >= self.config.scene_seconds:
            beat.scene_done = True
            forced_done = True

        world.beats += 1
        self._beat, self._clip_index, self._beat_no = beat, 0, world.beats
        self._beat_shape = shape
        self._beat_input = text
        self._pending_note = ""

        reason, mode = self._trigger(beat, forced_done)
        if (beat.scene_done or beat.needs_replan) and self._held_trigger is None:
            # One-shot: remembered until a replan run takes it, so a flag
            # raised while a run is in flight or inside the gap is not lost.
            self._held_trigger = (reason or "", mode, seg)
        self._pending_spawn = None
        spawning: str | None = None
        if reason:
            held = self._hold_reason(seg)
            if held:
                notes.append(f"showrunner trigger held ({reason}): {held}")
            else:
                # Spawned when this beat's first clip is emitted, after it is
                # applied, so the snapshot and the window include it.
                self._pending_spawn = (reason, mode)
                spawning = reason
        self._plan_notes = notes
        self._plan_meta = {"vo_open": vo_open, "pacing": pnote, "trigger": spawning, "forced": forced_done}
        return {"beat": world.beats, "clips": len(beat.clips), "director_s": round(director_s, 4),
                "retry": retried, "fallback": fallback, "bootstrap": bootstrap}

    def discard_pending_input(self, segment: int) -> None:
        """A plan was cancelled before it returned: forget the line it had
        recorded, so the restarted plan records the full line once."""
        if self.world is not None:
            self.world.drop_pending_interventions(segment)

    def abort_beat(self, reason: str) -> int:
        """Drop the clips of the current beat that have not been emitted.
        Returns how many were dropped. The next plan is told where the
        picture actually ends."""
        beat = self._beat
        if beat is None:
            return 0
        done, total = self._clip_index, len(beat.clips)
        dropped = max(0, total - done)
        if done == 0:
            self._pending_note = (
                f'The beat "{beat.intent}" was dropped before any of it played ({reason}). '
                + (f"The picture still ends on: {self._last_end_state}" if self._last_end_state
                   else "The picture is still the reference image.")
            )
        else:
            self._pending_note = (
                f'The previous beat "{beat.intent}" was cut short after clip {done} of {total} ({reason}); '
                f"its remaining clips were never rendered. The picture ends on: {self._last_end_state}"
            )
        self._last_beat = (beat.intent, done, total)
        self._beat = None
        self._clip_index = 0
        if done == 0 and self._pending_spawn is not None:
            # The clip the run was to follow never played. A replan flag
            # stays held; the other triggers are re-read at the next plan.
            self._showrunner_notes.append(
                f"showrunner ({self._pending_spawn[0]}): not spawned, the beat was cut before its first clip",
            )
            self._pending_spawn = None
        # Notes from the plan that never showed (a beat cut before its first clip) carry over.
        if done == 0 and self._plan_notes:
            self._showrunner_notes = self._plan_notes + self._showrunner_notes
            self._plan_notes = []
        self._showrunner_notes.append(
            f'beat "{beat.intent}" cut short after clip {done} of {total} ({reason}); {dropped} clip(s) dropped',
        )
        return dropped

    def _beat_status(self) -> str:
        if self._pending_note:
            return self._pending_note
        if self._last_beat is not None:
            intent, done, total = self._last_beat
            return (f'The previous beat "{intent}" ({done} clip{"s" if done != 1 else ""}) played out in full. '
                    f"The picture ends on: {self._last_end_state}")
        return "You are opening the film; the picture starts from the reference image."

    def _fallback_beat(self) -> BeatPlan:
        world = self.world
        assert world is not None
        hero = world.entities.get(world.protagonist)
        name = hero.name if hero else "The character"
        return BeatPlan(
            intent="holding the picture",
            framing="medium shot",
            clips=[ClipSpec(action=f"{name} holds still. Nothing else happens.",
                            end_state="unchanged", on_screen=[hero.id] if hero else [])],
        )

    # ── Emit: one clip of the beat ──────────────────────────────────

    async def emit(self, state: StoryState) -> StoryState:
        """Emit the next clip of the current beat as segment `state.turn_number`:
        apply its world changes, compile its prompt, fill the views, log it.
        With `fail_soft` the engine and world are kept as they were before
        the clip, so a clip that never renders can be taken back
        (`rollback_clip`); an exception here restores them, cuts the beat
        and raises `EmitFailed` instead of ending the film."""
        if not self.config.fail_soft:
            return await self._emit(state)
        snapshot = self._snapshot(state.turn_number)
        try:
            state = await self._emit(state)
        except Exception as exc:
            logger.exception("Emitting segment %s failed; rolling back", state.turn_number)
            self._restore(snapshot, [])
            self._cut_after_failure(snapshot, f"emitting it failed ({type(exc).__name__})")
            raise EmitFailed(f"segment {state.turn_number}: {type(exc).__name__}: {exc}") from exc
        self._emit_snapshot, self._landed_since = snapshot, []
        return state

    def rollback_clip(self, segment: int, reason: str) -> str:
        """Take back the last emitted clip, `segment`, whose render failed
        (RUN-09): the world and the beat return to before it (Showrunner
        updates landed since are applied again), the beat is cut, and the
        next plan is told the picture ends on the clip before. Returns the
        viewer text to answer again: the line the dropped beat answered, if
        the failed clip was its first, and the line of a beat planned since
        and never played; the lines' recorded interventions are dropped so
        the next plan records them once."""
        snap = self._emit_snapshot
        if snap is None or snap["segment"] != segment or self.world is None:
            logger.warning("No snapshot for segment %s; cannot roll back", segment)
            return ""
        carry = []
        if snap["clip_index"] == 0 and snap["beat_input"]:
            carry.append(snap["beat_input"])
        if self._beat is not None and self._clip_index == 0 and self._beat_input and self._beat is not snap["beat_ref"]:
            carry.append(self._beat_input)          # a beat planned since, never played
        self._restore(snap, self._landed_since)
        for seg in {segment, self.world.segment}:
            self.world.drop_pending_interventions(seg)
        self._cut_after_failure(snap, reason)
        self._emit_snapshot, self._landed_since = None, []
        if self.session_dir is not None:
            try:
                self.world.save(self.session_dir, rewrite=True)
            except OSError:
                logger.exception("Could not persist world model to %s", self.session_dir)
        return " ".join(carry)

    def _snapshot(self, segment: int) -> dict:
        return {
            "segment": segment,
            "world": self.world.model_copy(deep=True) if self.world is not None else None,
            "beat": self._beat.model_copy(deep=True) if self._beat is not None else None,
            "beat_ref": self._beat,
            "clip_index": self._clip_index, "beat_no": self._beat_no, "beat_input": self._beat_input,
            "plan_notes": list(self._plan_notes), "plan_meta": dict(self._plan_meta),
            "last_end_state": self._last_end_state, "last_beat": self._last_beat,
            "pending_note": self._pending_note, "pending_spawn": self._pending_spawn,
            "held_trigger": self._held_trigger, "beat_shape": self._beat_shape,
        }

    def _restore(self, snap: dict, landings: list[tuple]) -> None:
        self.world = snap["world"]
        self._beat, self._clip_index, self._beat_no = snap["beat"], snap["clip_index"], snap["beat_no"]
        self._beat_input, self._plan_notes, self._plan_meta = snap["beat_input"], snap["plan_notes"], snap["plan_meta"]
        self._last_end_state, self._last_beat = snap["last_end_state"], snap["last_beat"]
        self._pending_note, self._pending_spawn = snap["pending_note"], snap["pending_spawn"]
        self._held_trigger, self._beat_shape = snap["held_trigger"], snap["beat_shape"]
        for update, at_segment, force_replan in landings:
            apply_showrunner(self.world, update, at_segment=at_segment, force_replan=force_replan)

    def _cut_after_failure(self, snap: dict, reason: str) -> None:
        """The beat whose clip failed is cut there; the next plan starts from
        the picture before it."""
        beat = self._beat
        seg = snap["segment"]
        where = (f"The picture ends on: {self._last_end_state}" if self._last_end_state
                 else "The picture is still the reference image.")
        if beat is not None:
            done, total = self._clip_index, len(beat.clips)
            self._pending_note = (
                f'Clip {done + 1} of the beat "{beat.intent}" never reached the viewer ({reason}); '
                f"it and the rest of the beat were dropped and the world is as it was before it. {where}"
            )
            self._last_beat = (beat.intent, done, total)
        self._beat, self._clip_index, self._pending_spawn = None, 0, None
        self._showrunner_notes.append(f"segment {seg}: {reason}; rolled back to before it and replanned")

    async def _emit(self, state: StoryState) -> StoryState:
        world = self.world
        beat = self._beat
        assert world is not None and beat is not None and self._clip_index < len(beat.clips)
        i = self._clip_index
        clip = beat.clips[i]
        seg = state.turn_number
        world.segment = seg
        first = i == 0
        state.user_input = self._beat_input if first else ""

        report = apply_clip(beat, i, world, segment_no=seg)
        if first and beat.intervention == "absorbed" and self._beat_input:
            world.viewer_momentum = self._beat_input
            world.viewer_momentum_segment = seg
        compiler = compiler_prose.compile_clip if self.config.compiler == "prose" else compile_clip
        prompt = compiler(
            beat, i, world,
            visual_style=state.visual_style,
            visual_anchor=state.visual_anchor,
            narration_mode=self.config.narration,
            narrator=state.narrator,
            opening=(seg == 1),
            segment_no=seg,
        )
        shape = self._beat_shape
        state.current_shape = shape
        duration = shape.delivered_seconds
        voiceover = beat.voiceover if first else ""
        self._write_views(state, beat, clip, prompt, duration, voiceover, first)

        audio_path = ""
        if (
            voiceover
            and self.config.narration == "tts"
            and self.config.audio_enabled
            and self.tts is not None
        ):
            result = await self.tts.synthesize(voiceover, turn=seg)
            langfuse_event(
                "tts", input=voiceover, output=result,
                metadata={"turn": seg, "success": result is not None},
                level="DEFAULT" if result else "WARNING",
            )
            audio_path = result or ""
            # One render path, no spanning (AUD-04): a line longer than its
            # clip is rejected, not carried over the next clips.
            length = await probe_duration(audio_path) if audio_path else None
            if length is not None and length > duration + 0.05:
                self._plan_notes.append(
                    f"tts: the line runs {length:.1f} s, longer than the {seconds_label(duration)} s clip; not rendered",
                )
                audio_path = ""
        state.current_audio_path = audio_path

        card = world.plan.scene
        world.events.append(EventRecord(
            segment=seg,
            scene_id=card.id if card else 0,
            beat=self._beat_no,
            beat_intent=beat.intent,
            clip_index=i + 1,
            clip_count=len(beat.clips),
            user_input=state.user_input,
            intervention=beat.intervention if first else "none",
            narration=clip.action_text(),
            applied_ops=list(report.applied),
            thread_ops=[a for a in report.applied if a.startswith("thread ")],
            on_screen=list(clip.on_screen),
            end_state=clip.end_state,
            voiceover=voiceover,
            duration=duration,
        ))
        self._save()
        if first and self._pending_spawn is not None:
            reason, mode = self._pending_spawn
            self._pending_spawn = None
            self._spawn_showrunner(state, reason=reason, mode=mode, at_segment=seg)

        notes = self._plan_notes if first else []
        self._plan_notes = [] if first else self._plan_notes
        state.turn_notes = self._format_notes(beat, i, report, notes, first=first)
        self._tick(state)

        self._last_end_state = clip.end_state
        self._clip_index += 1
        if self._clip_index >= len(beat.clips):
            self._last_beat = (beat.intent, len(beat.clips), len(beat.clips))
            self._beat = None
            self._clip_index = 0
        return state

    async def drain(self, state: StoryState) -> None:
        await self._join_showrunner()
        self._save()

    # ── Setup ───────────────────────────────────────────────────────

    def _init_world(self, state: StoryState) -> WorldModel:
        """A fresh world from the blueprint. Resume is not built: every run
        creates its own session directory, and the `world.json` written
        there is for inspection (and `events.jsonl` for replay)."""
        return WorldModel.from_story(state)

    def _save(self) -> None:
        if self.world is not None and self.session_dir is not None:
            try:
                self.world.save(self.session_dir)
            except OSError:
                logger.exception("Could not persist world model to %s", self.session_dir)

    # ── Showrunner clock ───────────────────────────────────────────

    def _trigger(self, beat: BeatPlan, forced_done: bool) -> tuple[str | None, str]:
        world = self.world
        assert world is not None
        cfg = self.config
        if beat.scene_done:
            return ("scene_done" + (" (scene length)" if forced_done else ""), "replan")
        if beat.needs_replan:
            return ("needs_replan", "replan")
        if self._held_trigger is not None:
            reason, mode, since = self._held_trigger
            return (f"{reason} (held since segment {since})", mode)
        # The Director is told a shift is REQUIRED at 1.5x the threshold;
        # the Showrunner replans only at 2x, a beat later, so the two never
        # act on the same stall at once (ARCH-05).
        quiet = world.seconds_since_change()
        if (quiet >= SHOWRUNNER_REPLAN_AT * cfg.stagnation_seconds
                and world.segment - world.last_showrunner >= cfg.showrunner_min_gap):
            return (f"stagnation: {quiet:.0f} s without change", "replan")
        if world.segment - world.last_compacted >= cfg.compaction_interval:
            return ("compaction interval", "compact")
        return (None, "")

    def _spawn_showrunner(self, state: StoryState, *, reason: str, mode: str, at_segment: int) -> None:
        assert self.world is not None
        snapshot = self.world.model_copy(deep=True)
        state_snapshot = state.model_copy(deep=True)

        async def _task():
            return await showrunner_agent.run(
                world=snapshot, state=state_snapshot, llm=self.llm_showrunner,
                config=self.config, reason=reason, mode=mode,
            )

        self._showrunner_meta = (at_segment, mode == "replan", reason)
        self._showrunner_task = asyncio.create_task(_task())
        self._last_spawn = at_segment
        if mode == "replan":
            self._held_trigger = None

    async def _run_showrunner_now(self, state: StoryState, *, reason: str, mode: str, at_segment: int) -> None:
        """The bootstrap: the only synchronous run. Nothing is on air yet."""
        assert self.world is not None
        self._last_spawn = at_segment
        update = await showrunner_agent.run(
            world=self.world, state=state, llm=self.llm_showrunner,
            config=self.config, reason=reason, mode=mode,
        )
        self._land(update, at_segment=at_segment, force_replan=True, reason=reason)

    def _hold_reason(self, seg: int) -> str | None:
        """Why a Showrunner trigger must wait: a run in flight, or too soon
        after the last one. A finished run that has not landed yet lands
        first, so its update is never lost to a new spawn."""
        task = self._showrunner_task
        if task is not None and task.done():
            self._land_if_done(seg)
            task = self._showrunner_task
        if task is not None:
            spawned_at = self._showrunner_meta[0] if self._showrunner_meta else "?"
            return f"a run spawned at segment {spawned_at} is still in flight"
        gap = seg - self._last_spawn
        if gap < self.config.showrunner_min_gap:
            return f"{gap} segment(s) since the last run, minimum {self.config.showrunner_min_gap}"
        return None

    def _land_if_done(self, seg: int) -> None:
        """Land the in-flight Showrunner update if it has finished. Never
        waits: the fast clock does not pay for the slow one."""
        task, meta = self._showrunner_task, self._showrunner_meta
        if task is None:
            return
        at_segment, force_replan, reason = meta or (self.world.segment if self.world else 0, False, "?")
        if not task.done():
            self._showrunner_notes.append(
                f"showrunner ({reason}, spawned at segment {at_segment}): still running; continuing with the current plan",
            )
            return
        self._showrunner_task, self._showrunner_meta = None, None
        try:
            update = task.result()
        except Exception:
            logger.exception("Showrunner task failed; continuing with the current plan")
            self._showrunner_notes.append("showrunner: task failed; plan unchanged")
            return
        self._land(update, at_segment=at_segment, force_replan=force_replan, reason=reason, landed_at=seg)

    async def _join_showrunner(self) -> None:
        """Wait for the in-flight run and land it. Only `drain` (the end of a
        run) does this; plans use `_land_if_done`."""
        if self._showrunner_task is None:
            return
        task, meta = self._showrunner_task, self._showrunner_meta
        self._showrunner_task, self._showrunner_meta = None, None
        try:
            update = await task
        except Exception:
            logger.exception("Showrunner task failed; continuing with the current plan")
            self._showrunner_notes.append("showrunner: task failed; plan unchanged")
            return
        at_segment, force_replan, reason = meta or (self.world.segment if self.world else 0, False, "?")
        self._land(update, at_segment=at_segment, force_replan=force_replan, reason=reason)

    def _land(self, update, *, at_segment: int, force_replan: bool, reason: str, landed_at: int | None = None) -> None:
        assert self.world is not None
        if update is None:
            self._showrunner_notes.append(f"showrunner ({reason}): no update (parse failure)")
            if self.world.plan.scene is None:
                # Bootstrap must not leave the Director without a scene.
                from src.story.world import SceneCard
                self.world.plan.scene = SceneCard(
                    id=1, goal="Open the film on the protagonist and let the first idea arrive.",
                    tension="Nothing has happened yet.", exit_condition="The protagonist is engaged with something.",
                    candidate_beats=[], opened_at=at_segment + 1,
                )
                self.world.scene_started = at_segment + 1
            return
        landed = apply_showrunner(self.world, update, at_segment=at_segment, force_replan=force_replan)
        self._landed_since.append((update, at_segment, force_replan))
        if any(n.startswith("scene ") for n in landed):
            self._held_trigger = None   # a new card answers any replan still held
        card = self.world.plan.scene
        line = f"showrunner ({reason}): " + ("; ".join(landed) if landed else "nothing to land")
        if landed_at is not None and landed_at > at_segment + 1:
            line += f" · landed at segment {landed_at} (spawned at {at_segment})"
        if update.rationale.strip():
            line += f" · rationale: {update.rationale.strip()}"
        self._showrunner_notes.append(line)
        if card is not None and any(n.startswith("scene ") for n in landed):
            self._showrunner_notes.append(
                f"scene {card.id} card: goal: {card.goal} · tension: {card.tension} · "
                f"exit: {card.exit_condition} · intensity: {card.intensity}"
                + (" · beats: " + " | ".join(card.candidate_beats) if card.candidate_beats else "")
            )
        if self.world.plan.arc:
            self._showrunner_notes.append(
                "arc: " + " → ".join(
                    ("▶ " if i == self.world.plan.current_act else "") + a
                    for i, a in enumerate(self.world.plan.arc)
                )
            )

    # ── Views for the runner ───────────────────────────────────────

    def _write_views(self, state: StoryState, beat: BeatPlan, clip: ClipSpec, prompt: str,
                     duration: float, voiceover: str, first: bool) -> None:
        world = self.world
        assert world is not None
        action = clip.action_text()
        state.current_beat = Beat(
            narration=action,
            action=action,
            outcome=clip.end_state,
            short_term_narrative=beat.intent,
            long_term_narrative=None,
        )
        on_screen_names = []
        for eid in clip.on_screen:
            ent = world.resolve(eid)
            on_screen_names.append(ent.name if ent else eid)
        state.current_shot = Shot(
            i2v_prompt=prompt,
            on_screen=on_screen_names,
            camera=beat.framing,
            motion=clip.camera_motion,
            end_frame_description=clip.end_state,
            duration_seconds=duration,
        )
        state.current_commentary = Commentary(voiceover=voiceover)
        state.current_intervention = beat.intervention if first else "none"
        state.current_intervention_reason = beat.intervention_reason if first else ""
        state.current_beat_intent = beat.intent
        state.current_clip = f"{self._clip_index + 1}/{len(beat.clips)}"
        here = world.current_location()
        state.world_state = {
            "protagonist_location": here.id if here else "",
            "inventory": [p.name for p in world.held_props()],
            "present": [e.id for e in world.present_entities()],
        }
        card = world.plan.scene
        state.context_brief = f"scene {card.id}: {card.goal}" if card else ""

    def _format_notes(self, beat: BeatPlan, index: int, report: Report, notes: list[str], *, first: bool) -> str:
        world = self.world
        assert world is not None
        cfg = self.config
        card = world.plan.scene
        meta = self._plan_meta
        out = ["**World**\n"]
        line = f"- Beat {self._beat_no} \"{beat.intent}\" · clip {index + 1} of {len(beat.clips)}"
        if card is not None:
            line += f" · scene {card.id} · {world.scene_seconds():.0f} s of about {cfg.scene_seconds:.0f} s"
        out.append(line)
        if report.applied:
            out.append("- Applied: " + "; ".join(report.applied))
        else:
            out.append("- Applied: (no world change)")
        if report.warnings:
            out.append("- Warnings: " + "; ".join(report.warnings))
        hero = world.entities.get(world.protagonist)
        if hero is not None and hero.now():
            out.append(f"- Now: {hero.now()}")
        present = ", ".join(e.line() for e in world.present_entities()) or "(nothing)"
        out.append(f"- Present: {present}")
        dormant = world.dormant_props()
        if dormant:
            out.append("- Dormant: " + ", ".join(f"{e.id} (seg {e.last_seen})" for e in dormant))
        threads = world.open_threads()
        if threads:
            out.append("- Open threads: " + "; ".join(f"{t.id} {t.text}" for t in threads[-6:]))
        if first and beat.intervention != "none":
            out.append(f"- Viewer: {beat.intervention}" + (f" ({beat.intervention_reason})" if beat.intervention_reason else ""))
        elif first and world.viewer_momentum:
            out.append(f"- Viewer momentum (seg {world.viewer_momentum_segment}): \"{world.viewer_momentum}\"")
        if first:
            out.append(f"- Voice-over floor: {'open' if meta.get('vo_open') else 'closed'} · pacing: {meta.get('pacing', '')}")
        clip = beat.clips[index]
        if clip.dialogue:
            out.append("- Dialogue: " + " / ".join(f"{d.speaker}: {d.line}" for d in clip.dialogue))
        if first:
            flags = [f for f, v in (("scene_done", beat.scene_done), ("needs_replan", beat.needs_replan)) if v]
            if flags:
                out.append("- Flags: " + ", ".join(flags) + (" (forced by scene length)" if meta.get("forced") else ""))
            if meta.get("trigger"):
                out.append(f"- Showrunner spawned: {meta['trigger']}")
        for n in notes:
            out.append(f"- {n}")
        return "\n".join(out) + "\n"

    def _tick(self, state: StoryState) -> None:
        if not state.pacing_managed:
            tick_story_clock(state, self.config)


def build_director_engine(
    *, llm: LLMBackend, config: Config, tts: ElevenLabsTTS | None = None,
    session_dir: Path | str | None = None, timing: TimingLog | None = None,
    shapes: ShapeRegistry | None = None,
) -> DirectorEngine:
    return DirectorEngine(llm=llm, config=config, tts=tts, session_dir=session_dir, timing=timing, shapes=shapes)
