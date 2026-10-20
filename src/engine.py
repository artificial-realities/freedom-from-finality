"""The engine: two LLM roles on two clocks, everything else deterministic.

Two LLM roles on two clocks, everything else deterministic:

- **Director** — one structured call per **beat**: one action or state
  of the film that lasts one to a few clips. Reads the assembled context
  and returns a `BeatPlan`; the engine then emits its clips one per
  segment with no LLM on the way, applying each clip's world changes as
  it goes. The Director is called again when the beat has played out, or
  at once when the viewer speaks: the rest of the beat is dropped and the
  next call is told where the picture actually ends.
- **Showrunner** — an async task on a slow clock, triggered at a plan
  (bootstrap, scene exit, a replan flag, or every `compaction_interval`
  segments) and spawned when the beat's first clip
  has been applied, so its snapshot and its compaction window include
  the clip that caused it. At most one is in flight and runs are at
  least `showrunner_min_gap` segments apart; a replan flag that has to
  wait is remembered until a run takes it. Its update lands at the start
  of the first plan that finds it finished; nothing ever waits for it.
  Compacts the event log and writes the plan.

Code owns the rest: the typed `WorldModel`, validation and apply, the
prompt compiler, and persistence per clip. Whether the film is moving or
going in circles is the two roles' judgment, from what they read: no
code measures it.

For the runner the engine keeps the `StoryState` surface: it fills
`current_beat`, `current_shot` and `current_dialogue` as views of the
clip, which rendering, playback, history and the transcript read. The
live loop calls `plan`, `emit` and `abort_beat` itself so it can render
just in time and interrupt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from src.agents import director as director_agent
from src.i2v.shape import ClipShape
from src.agents import showrunner as showrunner_agent
from src.llm import build_backend
from src.models.config import Config
from src.models.responses import Beat, Shot
from src.models.story import Story
from src.state.story_state import StoryState
from src.story.compiler import compile_clip
from src.story.schemas import BeatPlan, ClipSpec
from src.story.validator import Report, apply_clip, apply_showrunner
from src.story.world import EventRecord, WorldModel
from src.util.timing_log import TimingLog

logger = logging.getLogger(__name__)


class EmitFailed(RuntimeError):
    """Emitting a clip raised; the engine restored the world and cut the beat,
    and the loop replans the segment."""


class DirectorEngine:
    def __init__(
        self,
        *,
        config: Config,
        session_dir: Path | str | None = None,
        timing: TimingLog | None = None,
        shape: ClipShape | None = None,
    ) -> None:
        self.config = config
        # The run's one clip shape: every clip is that long; nothing here
        # derives a length from seconds.
        self.shape = shape or ClipShape.from_config(config)
        self.session_dir = Path(session_dir) if session_dir else None
        # The run's timing log: each plan writes director_start/director_end.
        self.timing = timing or TimingLog(None)
        self.world: WorldModel | None = None
        self.story: Story | None = None     # the blueprint, set with the world
        self.llm_director = build_backend(config.llm_backend, config.director_model,
                                          reasoning_effort=config.director_reasoning_effort)
        self.llm_showrunner = build_backend(config.llm_backend, config.showrunner_model,
                                            reasoning_effort=config.showrunner_reasoning_effort)
        self._showrunner_task: asyncio.Task | None = None
        # (at_segment, force_replan, reason, triggered_at): `triggered_at` is the
        # segment whose plan first raised the trigger (a held replan keeps the
        # segment it was held since), which decides whether the card it lands
        # may clear the viewer's momentum.
        self._showrunner_meta: tuple[int, bool, str, int] | None = None
        self._showrunner_notes: list[str] = []
        self._last_spawn: int = 0   # segment the last Showrunner run was spawned at
        # A replan the Director asked for (scene_done / needs_replan) that no
        # run has taken yet: (reason, mode, segment first asked). `_trigger`
        # raises it again at every plan until a replan run spawns or a
        # replan lands.
        self._held_trigger: tuple[str, str, int] | None = None
        # The last plan answered a viewer line with a failed Director call.
        self.last_plan_fallback: bool = False
        # The trigger this plan decided on, spawned at the beat's first clip
        # after it is applied: (reason, mode, triggered_at).
        self._pending_spawn: tuple[str, str, int] | None = None
        # Fail-soft: the engine and world as they were before the
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
        self._plan_meta: dict = {}          # the trigger, for the transcript
        self._last_end_state: str = ""      # the image the last emitted clip ends on
        self._last_beat: tuple[str, int, int] | None = None   # (intent, clips emitted, clips planned)
        self._pending_note: str = ""        # how the previous beat ended, for the next call
        # A renderer restart that re-seeded the picture from the reference
        # (the last clip's tail could not be restored), for
        # the next call only.
        self._reseed_note: str = ""

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
        # A viewer's direction stays active until the next line or
        # for `viewer_momentum_seconds` of film, whichever comes first; while
        # it is active the Director does not see the card's candidate beats,
        # which return when it expires.
        if world.viewer_momentum:
            age = world.seconds_since(world.viewer_momentum_segment)
            if age >= self.config.viewer_momentum_seconds:
                notes.append(f"viewer momentum expired: the direction of segment {world.viewer_momentum_segment} "
                             f"is {age:.0f} s old")
                self.timing.write("momentum_expired", turn=seg, since=world.viewer_momentum_segment,
                                  age_s=round(age, 2))
                world.viewer_momentum = ""
                world.viewer_momentum_segment = 0
        text = (state.user_input or "").strip()
        if text:
            world.add_intervention(seg, text)

        bootstrap = world.plan.scene is None
        if bootstrap:
            await self._run_showrunner_now(state, reason="bootstrap: opening the film", mode="bootstrap", at_segment=seg - 1)
            notes.extend(self._showrunner_notes)
            self._showrunner_notes = []

        called = time.monotonic()
        beat, dnotes, retried = await director_agent.run(
            world=world, state=state, llm=self.llm_director, config=self.config,
            beat_status=self._beat_status(), clip_seconds=self.shape.delivered_seconds,
        )
        director_s = time.monotonic() - called
        self._reseed_note = ""
        notes.extend(dnotes)
        fallback = beat is None
        # Whether this plan answered a viewer line and the Director's call
        # failed: the loop then keeps the line for the next plan.
        self.last_plan_fallback = fallback and bool(text)
        if self.last_plan_fallback:
            # Taken back before the held clip is emitted, which would record
            # it as deferred; the next plan records and answers it once.
            world.drop_pending_interventions(seg)
        if beat is None:
            beat = self._fallback_beat()
            notes.append("director: call failed; holding the picture for one clip"
                         + ("; the viewer's line is answered at the next plan" if text else ""))
        beat.clips = beat.clips[: director_agent.clip_cap(self.config, text)]

        world.beats += 1
        self._beat, self._clip_index, self._beat_no = beat, 0, world.beats
        self._beat_input = text
        self._pending_note = ""

        reason, mode = self._trigger(beat)
        # Where the trigger arose: this plan, or the plan a held replan was
        # first asked at (`_trigger` raises a held one only when this beat
        # raised no flag of its own).
        triggered_at = seg
        if reason and self._held_trigger is not None and not (beat.scene_done or beat.needs_replan):
            triggered_at = self._held_trigger[2]
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
                self._pending_spawn = (reason, mode, triggered_at)
                spawning = reason
        self._plan_notes = notes
        self._plan_meta = {"trigger": spawning}
        cost = {"beat": world.beats, "clips": len(beat.clips), "director_s": round(director_s, 4),
                "retry": retried, "fallback": fallback, "bootstrap": bootstrap}
        self._record_plan(seg, beat, {"fallback": fallback, "bootstrap": bootstrap})
        return cost

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
                   else self._unplayed())
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

    def note_reseed(self, segment: int) -> None:
        """The renderer restarted and segment `segment` was rendered from the
        reference image, not from the picture before it:
        the next plan is told, since what the world model says is on screen
        is no longer in the picture."""
        self._reseed_note = (f"At segment {segment} the renderer restarted and the picture was re-seeded from "
                             f"{self._reference() or 'the reference image'}. Nothing from before is in the picture. "
                             "Bring back in this beat whatever the story needs on screen. ")

    def _reference(self) -> str:
        """The reference image in words, for the next plan ("" without one)."""
        ref = self.story.picture.reference if self.story is not None else None
        if ref is None:
            return ""
        shows = " ".join(ref.shows.split()).rstrip(".")
        return "the reference image" + (f", which shows {shows}" if shows else "")

    def _unplayed(self) -> str:
        """Where the picture is when no clip has played."""
        ref = self._reference()
        return f"The picture is still {ref}." if ref else "Nothing has played yet."

    def _beat_status(self) -> str:
        # The re-seed note stays until a plan has been answered (a cancelled
        # call is restarted with it).
        status = self._reseed_note + self._beat_status_plain()
        world = self.world
        if world is not None and world.cuts and world.shot:
            # Where cuts are allowed the shot can change between beats, so
            # the next beat is told which one the picture is in.
            status += f" The picture is in this shot: {world.shot}."
        return status

    def _beat_status_plain(self) -> str:
        if self._pending_note:
            return self._pending_note
        if self._last_beat is not None:
            intent, done, total = self._last_beat
            return (f'The previous beat "{intent}" ({done} clip{"s" if done != 1 else ""}) played out in full. '
                    f"The picture ends on: {self._last_end_state}")
        ref = self._reference()
        return "You are opening the film; " + (f"the picture starts as {ref}." if ref else
                                               "there is no reference image: the first clip starts from your words alone.")

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
        The engine and world are kept as they were before the clip, so a
        clip that never renders can be taken back (`rollback_clip`); an
        exception here restores them, cuts the beat and raises `EmitFailed`
        instead of ending the film."""
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
        """Take back the last emitted clip, `segment`, whose render failed:
        the world and the beat return to before it (Showrunner
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
            "held_trigger": self._held_trigger,
            "clip_counter": self.world._clip_counter if self.world is not None else 0,
            "last_clip_id": self.world._last_clip_id if self.world is not None else 0,
        }

    def _record_plan(self, segment: int, beat: BeatPlan, meta: dict) -> None:
        if self.session_dir is None:
            return
        path = self.session_dir / "plans.jsonl"
        record = {"segment": segment, "beat_no": self._beat_no,
                  "beat": beat.model_dump(mode="json"),
                  "input": self._beat_input or None, **meta}
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Could not write plan record to %s", path)

    def _record_showrunner(self, update, *, reason: str, at_segment: int, landed_at: int | None = None) -> None:
        if self.session_dir is None or update is None:
            return
        path = self.session_dir / "showrunner.jsonl"
        record = {"at_segment": at_segment, "reason": reason,
                  "update": update.model_dump(mode="json")}
        if landed_at is not None:
            record["landed_at"] = landed_at
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Could not write showrunner record to %s", path)

    def _restore(self, snap: dict, landings: list[tuple]) -> None:
        clip_counter = self.world._clip_counter if self.world is not None else 0
        self.world = snap["world"]
        self._beat, self._clip_index, self._beat_no = snap["beat"], snap["clip_index"], snap["beat_no"]
        self._beat_input, self._plan_notes, self._plan_meta = snap["beat_input"], snap["plan_notes"], snap["plan_meta"]
        self._last_end_state, self._last_beat = snap["last_end_state"], snap["last_beat"]
        self._pending_note, self._pending_spawn = snap["pending_note"], snap["pending_spawn"]
        self._held_trigger = snap["held_trigger"]
        # Clip counter must never go backwards; restore only the parent pointer.
        self.world._clip_counter = max(clip_counter, snap.get("clip_counter", 0))
        self.world._last_clip_id = snap.get("last_clip_id", 0)
        for update, at_segment, force_replan, triggered_at in landings:
            apply_showrunner(self.world, update, at_segment=at_segment, force_replan=force_replan,
                             triggered_at=triggered_at)

    def _cut_after_failure(self, snap: dict, reason: str) -> None:
        """The beat whose clip failed is cut there; the next plan starts from
        the picture before it."""
        beat = self._beat
        seg = snap["segment"]
        where = (f"The picture ends on: {self._last_end_state}" if self._last_end_state
                 else self._unplayed())
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

        parent_clip_id = world.last_clip_id
        clip_id = world.next_clip_id()

        report = apply_clip(beat, i, world, segment_no=seg)
        if first and beat.intervention == "absorbed" and self._beat_input:
            world.viewer_momentum = self._beat_input
            world.viewer_momentum_segment = seg
        prompt = compile_clip(beat, i, world, picture=state.story.picture, opening=(seg == 1), segment_no=seg)
        shape = self.shape
        state.current_shape = shape
        state.current_clip_id = clip_id
        state.current_parent_clip_id = parent_clip_id
        duration = shape.delivered_seconds
        said = [f"{world.resolve(d.speaker).name}: {d.line}" for d in clip.dialogue]   # apply_clip kept known speakers
        self._write_views(state, beat, clip, prompt, duration, said, first)

        card = world.plan.scene
        here = world.current_location()
        world.events.append(EventRecord(
            segment=seg,
            clip_id=clip_id,
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
            dialogue=said,
            location=here.id if here else "",
            duration=duration,
        ))
        self._save()
        if first and self._pending_spawn is not None:
            reason, mode, triggered_at = self._pending_spawn
            self._pending_spawn = None
            self._spawn_showrunner(state, reason=reason, mode=mode, at_segment=seg, triggered_at=triggered_at)

        notes = self._plan_notes if first else []
        self._plan_notes = [] if first else self._plan_notes
        state.turn_notes = self._format_notes(beat, i, report, notes, first=first)

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
        self.story = state.story
        return WorldModel.from_story(state.story)

    def _save(self) -> None:
        if self.world is not None and self.session_dir is not None:
            try:
                self.world.save(self.session_dir)
            except OSError:
                logger.exception("Could not persist world model to %s", self.session_dir)

    # ── Showrunner clock ───────────────────────────────────────────

    def _trigger(self, beat: BeatPlan) -> tuple[str | None, str]:
        world = self.world
        assert world is not None
        cfg = self.config
        if beat.scene_done:
            return ("scene_done", "replan")
        if beat.needs_replan:
            return ("needs_replan", "replan")
        if self._held_trigger is not None:
            reason, mode, since = self._held_trigger
            return (f"{reason} (held since segment {since})", mode)
        if world.segment - world.last_compacted >= cfg.compaction_interval:
            return ("compaction interval", "compact")
        return (None, "")

    def _spawn_showrunner(self, state: StoryState, *, reason: str, mode: str, at_segment: int,
                          triggered_at: int | None = None) -> None:
        assert self.world is not None
        snapshot = self.world.model_copy(deep=True)
        state_snapshot = state.model_copy(deep=True)

        async def _task():
            return await showrunner_agent.run(
                world=snapshot, state=state_snapshot, llm=self.llm_showrunner,
                config=self.config, reason=reason, mode=mode,
            )

        self._showrunner_meta = (at_segment, mode == "replan", reason,
                                 at_segment if triggered_at is None else triggered_at)
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
        seg0 = self.world.segment if self.world else 0
        at_segment, force_replan, reason, triggered_at = meta or (seg0, False, "?", seg0)
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
        self._land(update, at_segment=at_segment, force_replan=force_replan, reason=reason, landed_at=seg,
                   triggered_at=triggered_at)

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
        seg0 = self.world.segment if self.world else 0
        at_segment, force_replan, reason, triggered_at = meta or (seg0, False, "?", seg0)
        self._land(update, at_segment=at_segment, force_replan=force_replan, reason=reason,
                   triggered_at=triggered_at)

    def _land(self, update, *, at_segment: int, force_replan: bool, reason: str, landed_at: int | None = None,
              triggered_at: int | None = None) -> None:
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
        landed = apply_showrunner(self.world, update, at_segment=at_segment, force_replan=force_replan,
                                  triggered_at=triggered_at)
        self._landed_since.append((update, at_segment, force_replan, triggered_at))
        self._record_showrunner(update, reason=reason, at_segment=at_segment, landed_at=landed_at)
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
                f"exit: {card.exit_condition}"
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
                     duration: float, said: list[str], first: bool) -> None:
        world = self.world
        assert world is not None
        state.current_beat = Beat(narration=clip.action_text(), outcome=clip.end_state, intent=beat.intent)
        on_screen_names = []
        for eid in clip.on_screen:
            ent = world.resolve(eid)
            on_screen_names.append(ent.name if ent else eid)
        state.current_shot = Shot(
            i2v_prompt=prompt,
            on_screen=on_screen_names,
            camera=world.shot or beat.framing,
            motion=clip.camera_motion,
            end_frame_description=clip.end_state,
            duration_seconds=duration,
        )
        state.current_intervention = beat.intervention if first else "none"
        state.current_intervention_reason = beat.intervention_reason if first else ""
        state.current_beat_intent = beat.intent
        state.current_clip = f"{self._clip_index + 1}/{len(beat.clips)}"
        state.current_dialogue = said
        here = world.current_location()
        state.world_state = {
            "protagonist_location": here.id if here else "",
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
        if hero is not None and hero.state:
            out.append(f"- Now: {hero.state}")
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
        clip = beat.clips[index]
        if clip.dialogue:
            out.append("- Dialogue: " + " / ".join(f"{d.speaker}: {d.line}" for d in clip.dialogue))
        if first:
            flags = [f for f, v in (("scene_done", beat.scene_done), ("needs_replan", beat.needs_replan)) if v]
            if flags:
                out.append("- Flags: " + ", ".join(flags))
            if meta.get("trigger"):
                out.append(f"- Showrunner spawned: {meta['trigger']}")
        for n in notes:
            out.append(f"- {n}")
        return "\n".join(out) + "\n"

