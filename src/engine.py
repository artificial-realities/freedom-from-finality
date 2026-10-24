"""The engine: two LLM roles on two clocks, everything else deterministic.

- **Now** — one structured call per **beat**: one action or state of
  the story that lasts one to a few clips. Reads the assembled context
  and returns a `BeatPlan`; the engine then emits its clips one per
  segment with no LLM on the way, applying each clip's world changes as
  it goes. Now is called again when the beat has played out, or at
  once when the viewer speaks: the rest of the beat is dropped and the
  next call is told where the picture actually ends.
- **Then** — an async task on a slow clock, triggered at the bootstrap
  or every `compaction_interval` segments, spawned when the beat's first
  clip has been applied, so its snapshot and its compaction window include
  the clip that caused it. At most one is in flight and runs are at
  least `then_min_gap` segments apart. Its update lands at the start of
  the first plan that finds it finished; nothing ever waits for it.
  Compresses the event log and rewrites the notebook.

Code owns the rest: the typed `WorldModel`, validation and apply, the
prompt compiler, and persistence per clip.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from src.agents import now as now_agent
from src.agents import then as then_agent
from src.i2v.shape import ClipShape
from src.llm import OpenAIBackend
from src.models.config import Config
from src.models.responses import Beat, Shot
from src.models.story import FrameZero
from src.state.story_state import StoryState
from src.story.compiler import compile_clip
from src.story.schemas import BeatPlan, ClipSpec
from src.story.validator import Report, apply_clip, apply_then
from src.story.world import EventRecord, WorldModel
from src.util.timing_log import TimingLog

logger = logging.getLogger(__name__)


class EmitFailed(RuntimeError):
    """Emitting a clip raised; the engine restored the world and cut the beat,
    and the loop replans the segment."""


class NowEngine:
    def __init__(
        self,
        *,
        config: Config,
        session_dir: Path | str | None = None,
        timing: TimingLog | None = None,
        shape: ClipShape | None = None,
    ) -> None:
        self.config = config
        self.shape = shape or ClipShape.from_config(config)
        self.session_dir = Path(session_dir) if session_dir else None
        self.timing = timing or TimingLog(None)
        self.world: WorldModel | None = None
        self.story: FrameZero | None = None
        self.llm_now = OpenAIBackend(model=config.now_model, reasoning_effort=config.now_reasoning_effort)
        self.llm_then = OpenAIBackend(model=config.then_model, reasoning_effort=config.then_reasoning_effort)
        self._then_task: asyncio.Task | None = None
        self._then_meta: tuple[int, str] | None = None   # (at_segment, reason)
        self._then_notes: list[str] = []
        self._last_spawn: int = 0
        self.last_plan_fallback: bool = False
        self._pending_spawn: tuple[str, str] | None = None   # (reason, mode)
        self._emit_snapshot: dict | None = None
        self._landed_since: list[tuple] = []
        self._beat: BeatPlan | None = None
        self._clip_index: int = 0
        self._beat_no: int = 0
        self._beat_input: str = ""
        self._plan_notes: list[str] = []
        self._plan_meta: dict = {}
        self._last_end_state: str = ""
        self._last_beat: tuple[str, int, int] | None = None
        self._pending_note: str = ""
        self._reseed_note: str = ""

    def has_next_clip(self) -> bool:
        return self._beat is not None and self._clip_index < len(self._beat.clips)

    def beat_unplayed(self) -> bool:
        return self._beat is not None and self._clip_index == 0

    # ── Plan: the Now call ──────────────────────────────────────────

    async def plan(self, state: StoryState) -> None:
        seg = state.turn_number
        answers = bool((state.user_input or "").strip())
        started = time.monotonic()
        self.timing.write("now_start", turn=seg, input=answers)
        try:
            cost = await self._plan(state)
        except BaseException as exc:
            cancelled = isinstance(exc, asyncio.CancelledError)
            self.timing.write("now_end", turn=seg, input=answers, cancelled=cancelled,
                              plan_s=round(time.monotonic() - started, 4),
                              **({} if cancelled else {"error": type(exc).__name__}))
            raise
        self.timing.write("now_end", turn=seg, input=answers,
                          plan_s=round(time.monotonic() - started, 4), **cost)

    async def _plan(self, state: StoryState) -> dict:
        if self.world is None:
            self.world = self._init_world(state)
            self._last_spawn = self.world.last_then
        world = self.world
        seg = state.turn_number

        self._land_if_done(seg)
        notes: list[str] = list(self._then_notes)
        self._then_notes = []

        world.segment = seg
        text = (state.user_input or "").strip()
        if text:
            world.add_intervention(seg, text)

        bootstrap = world.then_runs == 0
        if bootstrap:
            await self._run_then_now(state, reason="bootstrap: opening the story", mode="bootstrap", at_segment=seg - 1)
            notes.extend(self._then_notes)
            self._then_notes = []

        called = time.monotonic()
        beat, dnotes, retried = await now_agent.run(
            world=world, state=state, llm=self.llm_now, config=self.config,
            beat_status=self._beat_status(), clip_seconds=self.shape.delivered_seconds,
        )
        now_s = time.monotonic() - called
        self._reseed_note = ""
        notes.extend(dnotes)
        fallback = beat is None
        self.last_plan_fallback = fallback and bool(text)
        if self.last_plan_fallback:
            world.drop_pending_interventions(seg)
        if beat is None:
            beat = self._fallback_beat()
            notes.append("now: call failed; holding the picture for one clip"
                         + ("; the viewer's line is answered at the next plan" if text else ""))
        beat.clips = beat.clips[: now_agent.clip_cap(self.config, text)]

        world.beats += 1
        self._beat, self._clip_index, self._beat_no = beat, 0, world.beats
        self._beat_input = text
        self._pending_note = ""

        reason, mode = self._trigger()
        self._pending_spawn = None
        spawning: str | None = None
        if reason:
            held = self._hold_reason(seg)
            if held:
                notes.append(f"then trigger held ({reason}): {held}")
            else:
                self._pending_spawn = (reason, mode)
                spawning = reason
        self._plan_notes = notes
        self._plan_meta = {"trigger": spawning}
        cost = {"beat": world.beats, "clips": len(beat.clips), "now_s": round(now_s, 4),
                "retry": retried, "fallback": fallback, "bootstrap": bootstrap}
        self._record_plan(seg, beat, {"fallback": fallback, "bootstrap": bootstrap})
        return cost

    def discard_pending_input(self, segment: int) -> None:
        if self.world is not None:
            self.world.drop_pending_interventions(segment)

    def abort_beat(self, reason: str) -> int:
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
            self._then_notes.append(
                f"then ({self._pending_spawn[0]}): not spawned, the beat was cut before its first clip",
            )
            self._pending_spawn = None
        if done == 0 and self._plan_notes:
            self._then_notes = self._plan_notes + self._then_notes
            self._plan_notes = []
        self._then_notes.append(
            f'beat "{beat.intent}" cut short after clip {done} of {total} ({reason}); {dropped} clip(s) dropped',
        )
        return dropped

    def note_reseed(self, segment: int) -> None:
        self._reseed_note = (f"At segment {segment} the renderer restarted and the picture was re-seeded from "
                             f"the first frame ({self._scene()}). Nothing from before is in the picture. "
                             "Bring back in this beat whatever the story needs on screen. ")

    def _scene(self) -> str:
        """What the frame zero's first frame shows. Now hears it only when
        the picture is that frame: at the opening, or after a re-seed."""
        return " ".join(self.story.scene.split()).rstrip(".") if self.story is not None else ""

    def _unplayed(self) -> str:
        return f"The picture is still the first frame: {self._scene()}."

    def _beat_status(self) -> str:
        status = self._reseed_note + self._beat_status_plain()
        world = self.world
        if world is not None and world.shot:
            status += f" The picture is in this shot: {world.shot}."
        return status

    def _beat_status_plain(self) -> str:
        if self._pending_note:
            return self._pending_note
        if self._last_beat is not None:
            intent, done, total = self._last_beat
            return (f'The previous beat "{intent}" ({done} clip{"s" if done != 1 else ""}) played out in full. '
                    f"The picture ends on: {self._last_end_state}")
        return (f"You are opening the story. The first frame shows: {self._scene()}. Open the action from exactly "
                "that picture, then move; anyone it does not show enters the picture.")

    def _fallback_beat(self) -> BeatPlan:
        world = self.world
        assert world is not None
        # Hold whatever the last clip showed, so nobody leaves the picture.
        last = world.events[-1].on_screen if world.events else (world.opening_picture or [])
        on_screen = [eid for eid in last if (e := world.entities.get(eid)) is not None and e.present and not e.gone]
        names = [world.entities[eid].name for eid in on_screen if world.entities[eid].kind == "character"]
        action = (f"{' and '.join(names)} hold{'s' if len(names) == 1 else ''} still. Nothing else happens."
                  if names else "The scene holds still. Nothing happens.")
        return BeatPlan(
            intent="holding the picture",
            framing="medium shot",
            clips=[ClipSpec(action=action, end_state="unchanged", on_screen=on_screen)],
        )

    # ── Emit: one clip of the beat ──────────────────────────────────

    async def emit(self, state: StoryState) -> StoryState:
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
        snap = self._emit_snapshot
        if snap is None or snap["segment"] != segment or self.world is None:
            logger.warning("No snapshot for segment %s; cannot roll back", segment)
            return ""
        carry = []
        if snap["clip_index"] == 0 and snap["beat_input"]:
            carry.append(snap["beat_input"])
        if self._beat is not None and self._clip_index == 0 and self._beat_input and self._beat is not snap["beat_ref"]:
            carry.append(self._beat_input)
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

    def _record_then(self, update, *, reason: str, at_segment: int, landed_at: int | None = None) -> None:
        if self.session_dir is None or update is None:
            return
        path = self.session_dir / "then.jsonl"
        record = {"at_segment": at_segment, "reason": reason,
                  "update": update.model_dump(mode="json")}
        if landed_at is not None:
            record["landed_at"] = landed_at
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Could not write then record to %s", path)

    def _restore(self, snap: dict, landings: list[tuple]) -> None:
        clip_counter = self.world._clip_counter if self.world is not None else 0
        self.world = snap["world"]
        self._beat, self._clip_index, self._beat_no = snap["beat"], snap["clip_index"], snap["beat_no"]
        self._beat_input, self._plan_notes, self._plan_meta = snap["beat_input"], snap["plan_notes"], snap["plan_meta"]
        self._last_end_state, self._last_beat = snap["last_end_state"], snap["last_beat"]
        self._pending_note, self._pending_spawn = snap["pending_note"], snap["pending_spawn"]
        self.world._clip_counter = max(clip_counter, snap.get("clip_counter", 0))
        self.world._last_clip_id = snap.get("last_clip_id", 0)
        for update, at_segment in landings:
            apply_then(self.world, update, at_segment=at_segment)

    def _cut_after_failure(self, snap: dict, reason: str) -> None:
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
        self._then_notes.append(f"segment {seg}: {reason}; rolled back to before it and replanned")

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
        prompt = compile_clip(beat, i, world, scene=state.story.scene, opening=(seg == 1), segment_no=seg)
        shape = self.shape
        state.current_shape = shape
        state.current_clip_id = clip_id
        state.current_parent_clip_id = parent_clip_id
        duration = shape.delivered_seconds
        said = [f"{world.resolve(d.speaker).name}: {d.line}" for d in clip.dialogue]
        self._write_views(state, beat, clip, prompt, duration, said, first)

        here = world.current_location()
        world.events.append(EventRecord(
            segment=seg,
            clip_id=clip_id,
            beat=self._beat_no,
            beat_intent=beat.intent,
            clip_index=i + 1,
            clip_count=len(beat.clips),
            user_input=state.user_input,
            intervention=beat.intervention if first else "none",
            narration=clip.action_text(),
            applied_ops=list(report.applied),
            on_screen=list(clip.on_screen),
            end_state=clip.end_state,
            dialogue=said,
            location=here.id if here else "",
            duration=duration,
        ))
        self._save()
        if first and self._pending_spawn is not None:
            reason, mode = self._pending_spawn
            self._pending_spawn = None
            self._spawn_then(state, reason=reason, mode=mode, at_segment=seg)

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
        await self._join_then()
        self._save()

    # ── Setup ───────────────────────────────────────────────────────

    def _init_world(self, state: StoryState) -> WorldModel:
        self.story = state.story
        return WorldModel.from_frame_zero(state.story)

    def _save(self) -> None:
        if self.world is not None and self.session_dir is not None:
            try:
                self.world.save(self.session_dir)
            except OSError:
                logger.exception("Could not persist world model to %s", self.session_dir)

    # ── Then clock ─────────────────────────────────────────────────

    def _trigger(self) -> tuple[str | None, str]:
        world = self.world
        assert world is not None
        if world.segment - world.last_compacted >= self.config.compaction_interval:
            return ("compaction interval", "compact")
        return (None, "")

    def _spawn_then(self, state: StoryState, *, reason: str, mode: str, at_segment: int) -> None:
        assert self.world is not None
        snapshot = self.world.model_copy(deep=True)
        state_snapshot = state.model_copy(deep=True)

        async def _task():
            return await then_agent.run(
                world=snapshot, state=state_snapshot, llm=self.llm_then,
                config=self.config, reason=reason, mode=mode,
            )

        self._then_meta = (at_segment, reason)
        self._then_task = asyncio.create_task(_task())
        self._last_spawn = at_segment

    async def _run_then_now(self, state: StoryState, *, reason: str, mode: str, at_segment: int) -> None:
        """The bootstrap: the only synchronous run."""
        assert self.world is not None
        self._last_spawn = at_segment
        update = await then_agent.run(
            world=self.world, state=state, llm=self.llm_then,
            config=self.config, reason=reason, mode=mode,
        )
        self._land(update, at_segment=at_segment, reason=reason)

    def _hold_reason(self, seg: int) -> str | None:
        task = self._then_task
        if task is not None and task.done():
            self._land_if_done(seg)
            task = self._then_task
        if task is not None:
            spawned_at = self._then_meta[0] if self._then_meta else "?"
            return f"a run spawned at segment {spawned_at} is still in flight"
        gap = seg - self._last_spawn
        if gap < self.config.then_min_gap:
            return f"{gap} segment(s) since the last run, minimum {self.config.then_min_gap}"
        return None

    def _land_if_done(self, seg: int) -> None:
        task, meta = self._then_task, self._then_meta
        if task is None:
            return
        seg0 = self.world.segment if self.world else 0
        at_segment, reason = meta or (seg0, "?")
        if not task.done():
            self._then_notes.append(
                f"then ({reason}, spawned at segment {at_segment}): still running; continuing with the current notebook",
            )
            return
        self._then_task, self._then_meta = None, None
        try:
            update = task.result()
        except Exception:
            logger.exception("Then task failed; continuing with the current notebook")
            self._then_notes.append("then: task failed; notebook unchanged")
            return
        self._land(update, at_segment=at_segment, reason=reason, landed_at=seg)

    async def _join_then(self) -> None:
        if self._then_task is None:
            return
        task, meta = self._then_task, self._then_meta
        self._then_task, self._then_meta = None, None
        try:
            update = await task
        except Exception:
            logger.exception("Then task failed; continuing with the current notebook")
            self._then_notes.append("then: task failed; notebook unchanged")
            return
        seg0 = self.world.segment if self.world else 0
        at_segment, reason = meta or (seg0, "?")
        self._land(update, at_segment=at_segment, reason=reason)

    def _land(self, update, *, at_segment: int, reason: str, landed_at: int | None = None) -> None:
        assert self.world is not None
        if update is None:
            self._then_notes.append(f"then ({reason}): no update (parse failure)")
            return
        landed = apply_then(self.world, update, at_segment=at_segment)
        self._landed_since.append((update, at_segment))
        self._record_then(update, reason=reason, at_segment=at_segment, landed_at=landed_at)
        line = f"then ({reason}): " + ("; ".join(landed) if landed else "nothing to land")
        if landed_at is not None and landed_at > at_segment + 1:
            line += f" · landed at segment {landed_at} (spawned at {at_segment})"
        if update.rationale.strip():
            line += f" · rationale: {update.rationale.strip()}"
        self._then_notes.append(line)

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
            "camera_location": here.id if here else "",
            "present": [e.id for e in world.present_entities()],
        }

    def _format_notes(self, beat: BeatPlan, index: int, report: Report, notes: list[str], *, first: bool) -> str:
        world = self.world
        assert world is not None
        meta = self._plan_meta
        out = ["**World**\n"]
        line = f"- Beat {self._beat_no} \"{beat.intent}\" · clip {index + 1} of {len(beat.clips)}"
        out.append(line)
        if report.applied:
            out.append("- Applied: " + "; ".join(report.applied))
        else:
            out.append("- Applied: (no world change)")
        if report.warnings:
            out.append("- Warnings: " + "; ".join(report.warnings))
        present = ", ".join(e.line() for e in world.present_entities()) or "(nothing)"
        out.append(f"- Present: {present}")
        dormant = world.dormant_props()
        if dormant:
            out.append("- Dormant: " + ", ".join(f"{e.id} (seg {e.last_seen})" for e in dormant))
        if first and beat.intervention != "none":
            out.append(f"- Viewer: {beat.intervention}" + (f" ({beat.intervention_reason})" if beat.intervention_reason else ""))
        elif first and world.viewer_momentum:
            out.append(f"- Viewer momentum (seg {world.viewer_momentum_segment}): \"{world.viewer_momentum}\"")
        clip = beat.clips[index]
        if clip.dialogue:
            out.append("- Dialogue: " + " / ".join(f"{d.speaker}: {d.line}" for d in clip.dialogue))
        if first and meta.get("trigger"):
            out.append(f"- Then spawned: {meta['trigger']}")
        for n in notes:
            out.append(f"- {n}")
        return "\n".join(out) + "\n"


