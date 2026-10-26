"""The engine: two LLM roles on two clocks, everything else deterministic.

- **Now** — one structured call per **beat**: one action or state of
  the story that lasts one to a few clips. Reads the assembled context
  and returns a `BeatPlan`; the engine then emits its clips one at a
  time with no LLM on the way, applying each clip's world changes as it
  goes. Now is called again when the beat's last clip has been emitted,
  or at once when the viewer types: the rest of the beat is dropped and
  the next call is told where the picture actually ends.
- **Then** — once before the story's first clip, then an async task on
  a slow clock: every `then_every` clips, spawned when the beat's first
  clip has been applied, so its snapshot and the clips it reads include
  the clip that caused it. At most one is in flight and runs are at
  least `then_min_gap` clips apart. Its update lands at the start of the
  first plan that finds it finished; nothing ever waits for it.
  Summarises the clips since its last run, rewrites the notebook,
  rewrites its understanding of the viewer, and may name a checkpoint
  where the story turned.

Code owns the rest: the typed `WorldModel`, validation and apply, the
prompt compiler, the viewer's memory (each input as it arrives, and what
the story did with it once that clip is on air, never rolled back with
the world), and the story folder: it is saved as of each clip the viewer
sees (`commit`) and resumed from the last one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from pydantic import BaseModel, Field

from src.agents import now as now_agent
from src.agents import then as then_agent
from src.i2v.shape import ClipShape
from src.llm import OpenAIBackend
from src.models.config import Config
from src.story.compiler import compile_clip
from src.story.schemas import BeatPlan, ClipSpec, ThenUpdate
from src.story.validator import Report, apply_clip, apply_then
from src.story.viewer import Outcome
from src.story.world import ClipRecord
from src.story_folder import StoryFolder
from src.util.timing_log import TimingLog

logger = logging.getLogger(__name__)


class EmitFailed(RuntimeError):
    """Emitting a clip raised; the engine restored the world and cut the beat,
    and the loop replans the clip."""


class Emitted(BaseModel):
    """A clip the engine has just emitted: what the loop renders and plays,
    and what the transcript shows. `input` is the viewer's input the beat
    answers, on its first clip; `notes` the world as the transcript reads
    it."""

    clip: int
    clip_id: int
    parent_clip_id: int
    prompt: str
    action: str
    end_state: str
    dialogue: list[str] = Field(default_factory=list)
    on_screen: list[str] = Field(default_factory=list)
    camera: str = ""
    motion: str = ""
    duration: float
    input: str = ""
    notes: str = ""


class Engine:
    def __init__(
        self,
        *,
        config: Config,
        folder: StoryFolder,
        film_dir: Path | str | None = None,
        timing: TimingLog | None = None,
        shape: ClipShape | None = None,
    ) -> None:
        self.config = config
        self.shape = shape or ClipShape.from_config(config)
        self.film_dir = Path(film_dir) if film_dir else None
        self.timing = timing or TimingLog(None)
        self.folder = folder
        self.world = folder.world
        self.frame_zero = folder.frame_zero
        self.viewer = folder.viewer
        self.llm_now = OpenAIBackend(model=config.now_model, reasoning_effort=config.now_reasoning_effort)
        self.llm_then = OpenAIBackend(model=config.then_model, reasoning_effort=config.then_reasoning_effort)
        # Each emitted clip until it goes on air: the world as of it, its
        # record, and what it did with the viewer's inputs. Committed then,
        # dropped if it never airs.
        self._on_air: dict[int, tuple[str, str, list[Outcome]]] = {}
        self._then_task: asyncio.Task | None = None
        # (at_clip, reason, records read)
        self._then_meta: tuple[int, str, int] | None = None
        self._then_notes: list[str] = []
        self._last_spawn: int = 0
        self.last_plan_fallback: bool = False
        self._pending_spawn: str | None = None
        self._emit_snapshot: dict | None = None
        self._landed_since: list[tuple[ThenUpdate, int]] = []
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
        self._last_spawn = self.world.last_then
        self._resume()

    def _resume(self) -> None:
        """Pick up where the viewer left off: the picture ends on the last
        clip they saw, and inputs they typed after the story last answered
        them are owed."""
        world = self.world
        if not world.clips:
            return
        last = world.clips[-1]
        self._last_end_state = last.end_state
        self._last_beat = (last.beat_intent, last.beat_clip, last.beat_clips)
        if last.beat_clip < last.beat_clips:
            self._pending_note = (f'The beat "{last.beat_intent}" was cut short after clip {last.beat_clip} of '
                                  f"{last.beat_clips}; its remaining clips were never shown. "
                                  f"The picture ends on: {last.end_state}")
        unanswered = self.viewer.unanswered()
        if unanswered:
            ids = [line.id for line in unanswered]
            item = world.add_input(world.clip, " ".join(line.text for line in unanswered), inputs=ids)
            item.owed, item.reason = True, "typed just before the story stopped, not answered yet"
            self.viewer.add(Outcome(inputs=ids, clip=world.clip, outcome="deferred", reason=item.reason))

    def has_next_clip(self) -> bool:
        return self._beat is not None and self._clip_index < len(self._beat.clips)

    def beat_unplayed(self) -> bool:
        return self._beat is not None and self._clip_index == 0

    # ── Plan: the Now call ──────────────────────────────────────────

    async def plan(self, clip_no: int, text: str = "", inputs: list[int] = ()) -> None:
        """Ask Now for the beat that starts at clip `clip_no`, answering
        `text`, the viewer's input (`inputs`: the ids of its lines in the
        viewer's memory), or none."""
        text = (text or "").strip()
        started = time.monotonic()
        self.timing.write("now_start", clip=clip_no, input=bool(text))
        try:
            cost = await self._plan(clip_no, text, list(inputs))
        except BaseException as exc:
            cancelled = isinstance(exc, asyncio.CancelledError)
            self.timing.write("now_end", clip=clip_no, input=bool(text), cancelled=cancelled,
                              plan_s=round(time.monotonic() - started, 4),
                              **({} if cancelled else {"error": type(exc).__name__}))
            raise
        self.timing.write("now_end", clip=clip_no, input=bool(text),
                          plan_s=round(time.monotonic() - started, 4), **cost)

    async def _plan(self, clip_no: int, text: str, inputs: list[int]) -> dict:
        world = self.world

        self._land_if_done(clip_no)
        notes: list[str] = list(self._then_notes)
        self._then_notes = []

        world.clip = clip_no
        if text:
            world.add_input(clip_no, text, inputs=inputs)

        opening = world.then_runs == 0
        if opening:
            await self._run_then_now(reason="the story opens", at_clip=clip_no - 1)
            notes.extend(self._then_notes)
            self._then_notes = []

        called = time.monotonic()
        beat, dnotes, retried = await now_agent.run(
            world=world, viewer=self.viewer, text=text, llm=self.llm_now, config=self.config,
            beat_status=self._beat_status(), clip_seconds=self.shape.delivered_seconds,
        )
        now_s = time.monotonic() - called
        self._reseed_note = ""
        notes.extend(dnotes)
        fallback = beat is None
        self.last_plan_fallback = fallback and bool(text)
        if self.last_plan_fallback:
            world.drop_pending_inputs(clip_no)
        if beat is None:
            beat = self._fallback_beat()
            notes.append("now: call failed; holding the picture for one clip"
                         + ("; the viewer's input is answered at the next plan" if text else ""))
        beat.clips = beat.clips[: now_agent.clip_cap(self.config, text)]

        world.beats += 1
        self._beat, self._clip_index, self._beat_no = beat, 0, world.beats
        self._beat_input = text
        self._pending_note = ""

        reason = self._trigger()
        self._pending_spawn = None
        spawning: str | None = None
        if reason:
            held = self._hold_reason(clip_no)
            if held:
                notes.append(f"then trigger held ({reason}): {held}")
            else:
                self._pending_spawn = reason
                spawning = reason
        self._plan_notes = notes
        self._plan_meta = {"trigger": spawning}
        cost = {"beat": world.beats, "clips": len(beat.clips), "now_s": round(now_s, 4),
                "retry": retried, "fallback": fallback, "opening": opening}
        self._record_plan(clip_no, beat, {"fallback": fallback, "opening": opening})
        return cost

    def discard_pending_input(self, clip_no: int) -> None:
        self.world.drop_pending_inputs(clip_no)

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
                f"then ({self._pending_spawn}): not spawned, the beat was cut before its first clip",
            )
            self._pending_spawn = None
        if done == 0 and self._plan_notes:
            self._then_notes = self._plan_notes + self._then_notes
            self._plan_notes = []
        self._then_notes.append(
            f'beat "{beat.intent}" cut short after clip {done} of {total} ({reason}); {dropped} clip(s) dropped',
        )
        return dropped

    def note_reseed(self, clip_no: int) -> None:
        self._reseed_note = (f"At clip {clip_no} the renderer restarted and the picture was re-seeded from "
                             f"the first frame ({self._scene()}). Nothing from before is in the picture. "
                             "Bring back in this beat whatever the story needs on screen. ")

    def _scene(self) -> str:
        """What Frame Zero's first frame shows. Now hears it only when the
        picture is that frame: at the opening, or after a re-seed."""
        return " ".join(self.frame_zero.scene.split()).rstrip(".")

    def _unplayed(self) -> str:
        return f"The picture is still the first frame: {self._scene()}."

    def _beat_status(self) -> str:
        status = self._reseed_note + self._beat_status_plain()
        if self.world.shot:
            status += f" The picture is in this shot: {self.world.shot}."
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
        # Hold whatever the last clip showed, so nobody leaves the picture.
        last = world.clips[-1].on_screen if world.clips else (world.opening_picture or [])
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

    async def emit(self, clip_no: int) -> Emitted:
        snapshot = self._snapshot(clip_no)
        try:
            emitted = await self._emit(clip_no)
        except Exception as exc:
            logger.exception("Emitting clip %s failed; rolling back", clip_no)
            self._on_air.pop(clip_no, None)
            self._restore(snapshot, [])
            self._cut_after_failure(snapshot, f"emitting it failed ({type(exc).__name__})")
            raise EmitFailed(f"clip {clip_no}: {type(exc).__name__}: {exc}") from exc
        self._emit_snapshot, self._landed_since = snapshot, []
        return emitted

    def rollback_clip(self, clip_no: int, reason: str) -> str:
        snap = self._emit_snapshot
        if snap is None or snap["clip"] != clip_no:
            logger.warning("No snapshot for clip %s; cannot roll back", clip_no)
            return ""
        carry = []
        if snap["clip_index"] == 0 and snap["beat_input"]:
            carry.append(snap["beat_input"])
        if self._beat is not None and self._clip_index == 0 and self._beat_input and self._beat is not snap["beat_ref"]:
            carry.append(self._beat_input)
        self._on_air.pop(clip_no, None)
        self._restore(snap, self._landed_since)
        for n in {clip_no, self.world.clip}:
            self.world.drop_pending_inputs(n)
        self._cut_after_failure(snap, reason)
        self._emit_snapshot, self._landed_since = None, []
        return " ".join(carry)

    def _snapshot(self, clip_no: int) -> dict:
        return {
            "clip": clip_no,
            "world": self.world.model_copy(deep=True),
            "beat": self._beat.model_copy(deep=True) if self._beat is not None else None,
            "beat_ref": self._beat,
            "clip_index": self._clip_index, "beat_no": self._beat_no, "beat_input": self._beat_input,
            "plan_notes": list(self._plan_notes), "plan_meta": dict(self._plan_meta),
            "last_end_state": self._last_end_state, "last_beat": self._last_beat,
            "pending_note": self._pending_note, "pending_spawn": self._pending_spawn,
        }

    def _record_plan(self, clip_no: int, beat: BeatPlan, meta: dict) -> None:
        if self.film_dir is None:
            return
        path = self.film_dir / "plans.jsonl"
        record = {"clip": clip_no, "beat_no": self._beat_no,
                  "beat": beat.model_dump(mode="json"),
                  "input": self._beat_input or None, **meta}
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Could not write plan record to %s", path)

    def _record_then(self, update, *, reason: str, at_clip: int, landed_at: int | None = None) -> None:
        if self.film_dir is None or update is None:
            return
        path = self.film_dir / "then.jsonl"
        record = {"at_clip": at_clip, "reason": reason,
                  "update": update.model_dump(mode="json")}
        if landed_at is not None:
            record["landed_at"] = landed_at
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Could not write then record to %s", path)

    def _restore(self, snap: dict, landings: list[tuple[ThenUpdate, int]]) -> None:
        # Clip ids are never reused, not even a rolled-back clip's.
        clip_counter = self.world.clip_counter
        self.world = snap["world"]
        self.world.clip_counter = max(clip_counter, self.world.clip_counter)
        self._beat, self._clip_index, self._beat_no = snap["beat"], snap["clip_index"], snap["beat_no"]
        self._beat_input, self._plan_notes, self._plan_meta = snap["beat_input"], snap["plan_notes"], snap["plan_meta"]
        self._last_end_state, self._last_beat = snap["last_end_state"], snap["last_beat"]
        self._pending_note, self._pending_spawn = snap["pending_note"], snap["pending_spawn"]
        for update, at_clip in landings:
            apply_then(self.world, update, at_clip=at_clip)

    def _cut_after_failure(self, snap: dict, reason: str) -> None:
        beat = self._beat
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
        self._then_notes.append(f"clip {snap['clip']}: {reason}; rolled back to before it and replanned")

    async def _emit(self, clip_no: int) -> Emitted:
        world = self.world
        beat = self._beat
        assert beat is not None and self._clip_index < len(beat.clips)
        i = self._clip_index
        spec = beat.clips[i]
        world.clip = clip_no
        first = i == 0
        text = self._beat_input if first else ""

        parent_clip_id = world.last_clip_id
        clip_id = world.next_clip_id()

        report = apply_clip(beat, i, world, clip_no=clip_no)
        heard = [Outcome(inputs=ids, clip=clip_no, outcome=how, reason=why, beat=beat.intent)
                 for ids, how, why in report.outcomes if ids]
        prompt = compile_clip(beat, i, world, scene=self.frame_zero.scene, opening=(clip_no == 1), clip_no=clip_no)
        duration = self.shape.delivered_seconds
        said = [f"{world.resolve(d.speaker).name}: {d.line}" for d in spec.dialogue]

        here = world.current_location()
        world.clips.append(ClipRecord(
            clip=clip_no,
            clip_id=clip_id,
            beat=self._beat_no,
            beat_intent=beat.intent,
            beat_clip=i + 1,
            beat_clips=len(beat.clips),
            input=text,
            taken=beat.input if first else "none",
            action=spec.action_text(),
            changes=list(report.applied),
            on_screen=list(spec.on_screen),
            end_state=spec.end_state,
            dialogue=said,
            location=here.id if here else "",
            duration=duration,
        ))
        world.trim_clips()
        self._on_air[clip_no] = (world.model_dump_json(), world.clips[-1].model_dump_json(), heard)
        if first and self._pending_spawn is not None:
            reason, self._pending_spawn = self._pending_spawn, None
            self._spawn_then(reason=reason, at_clip=clip_no)

        notes = self._plan_notes if first else []
        self._plan_notes = [] if first else self._plan_notes
        emitted = Emitted(
            clip=clip_no, clip_id=clip_id, parent_clip_id=parent_clip_id, prompt=prompt,
            action=spec.action_text(), end_state=spec.end_state, dialogue=said,
            on_screen=[e.name if (e := world.resolve(eid)) else eid for eid in spec.on_screen],
            camera=world.shot or beat.framing, motion=spec.camera_motion, duration=duration, input=text,
            notes=self._format_notes(beat, i, report, notes, first=first),
        )

        self._last_end_state = spec.end_state
        self._clip_index += 1
        if self._clip_index >= len(beat.clips):
            self._last_beat = (beat.intent, len(beat.clips), len(beat.clips))
            self._beat = None
            self._clip_index = 0
        return emitted

    # ── The viewer's memory ─────────────────────────────────────────

    def heard(self, text: str, on_air: int | None) -> int:
        """Record a viewer input as it arrives, with the clip on air then:
        what it showed, and who and what was in it or named by the input.
        Returns the input's id in the viewer's memory."""
        world = self.world
        shown = next((c for c in reversed(world.clips) if c.clip == on_air), None) if on_air else None
        entities = set(shown.on_screen) if shown is not None else set()
        entities |= world.mentions(text)
        line = self.viewer.hear(text, clip=on_air or 0, during=shown.action if shown is not None else "",
                                entities=entities)
        return line.id

    async def commit(self, clip_no: int, last_frames: Path | None) -> bool:
        """Clip `clip_no` is on air: save the story as of it (with
        `last_frames`, the frames that continue it, for video), then record
        what it did with the viewer's inputs. False when there was nothing
        to commit (a clip rolled back, or one this engine did not emit)."""
        pending = self._on_air.pop(clip_no, None)
        if pending is None:
            return False
        world_json, clip_json, heard = pending
        await asyncio.to_thread(self.folder.commit, clip_no, world_json, clip_json, last_frames)
        for record in heard:
            self.viewer.add(record)
        return True

    def close(self) -> None:
        """The film stopped. A Then run in flight is dropped: its work is
        redone after resuming, since nothing past the last aired clip is
        saved."""
        if self._then_task is not None:
            self._then_task.cancel()
            self._then_task, self._then_meta = None, None

    # ── Then clock ─────────────────────────────────────────────────

    def _trigger(self) -> str | None:
        if self.world.clip - self.world.last_summary >= self.config.then_every:
            return f"every {self.config.then_every} clips"
        return None

    def _spawn_then(self, *, reason: str, at_clip: int) -> None:
        snapshot = self.world.model_copy(deep=True)
        upto = len(self.viewer.records)

        async def _task():
            return await then_agent.run(
                world=snapshot, viewer=self.viewer, upto=upto, scene=self.frame_zero.scene, llm=self.llm_then,
                config=self.config, mode="summary", clip_seconds=self.shape.delivered_seconds,
            )

        self._then_meta = (at_clip, reason, upto)
        self._then_task = asyncio.create_task(_task())
        self._last_spawn = at_clip

    async def _run_then_now(self, *, reason: str, at_clip: int) -> None:
        """The opening run: the only synchronous one."""
        self._last_spawn = at_clip
        upto = len(self.viewer.records)
        update = await then_agent.run(
            world=self.world, viewer=self.viewer, upto=upto, scene=self.frame_zero.scene, llm=self.llm_then,
            config=self.config, mode="opening", clip_seconds=self.shape.delivered_seconds,
        )
        self._land(update, at_clip=at_clip, reason=reason, upto=upto)

    def _hold_reason(self, clip_no: int) -> str | None:
        task = self._then_task
        if task is not None and task.done():
            self._land_if_done(clip_no)
            task = self._then_task
        if task is not None:
            spawned_at = self._then_meta[0] if self._then_meta else "?"
            return f"a run spawned at clip {spawned_at} is still in flight"
        gap = clip_no - self._last_spawn
        if gap < self.config.then_min_gap:
            return f"{gap} clip(s) since the last run, minimum {self.config.then_min_gap}"
        return None

    def _land_if_done(self, clip_no: int) -> None:
        task, meta = self._then_task, self._then_meta
        if task is None:
            return
        at_clip, reason, upto = meta or (self.world.clip, "?", 0)
        if not task.done():
            self._then_notes.append(
                f"then ({reason}, spawned at clip {at_clip}): still running; continuing with the current notebook",
            )
            return
        self._then_task, self._then_meta = None, None
        try:
            update = task.result()
        except Exception:
            logger.exception("Then task failed; continuing with the current notebook")
            self._then_notes.append("then: task failed; notebook unchanged")
            return
        self._land(update, at_clip=at_clip, reason=reason, upto=upto, landed_at=clip_no)

    def _land(self, update: ThenUpdate | None, *, at_clip: int, reason: str, upto: int,
              landed_at: int | None = None) -> None:
        """Land a Then update: the world's part (re-applied if the world is
        rolled back) and the viewer's part (landed once: their memory is
        never rolled back)."""
        if update is None:
            self._then_notes.append(f"then ({reason}): no update (parse failure)")
            return
        landed = apply_then(self.world, update, at_clip=at_clip)
        landed += self.viewer.land(update, self.world, at_clip=at_clip, seen=upto)
        self._landed_since.append((update, at_clip))
        self._record_then(update, reason=reason, at_clip=at_clip, landed_at=landed_at)
        line = f"then ({reason}): " + ("; ".join(landed) if landed else "nothing to land")
        if landed_at is not None and landed_at > at_clip + 1:
            line += f" · landed at clip {landed_at} (spawned at {at_clip})"
        if update.rationale.strip():
            line += f" · rationale: {update.rationale.strip()}"
        self._then_notes.append(line)

    # ── The transcript ─────────────────────────────────────────────

    def _format_notes(self, beat: BeatPlan, index: int, report: Report, notes: list[str], *, first: bool) -> str:
        world = self.world
        meta = self._plan_meta
        out = ["**World**\n"]
        out.append(f"- Beat {self._beat_no} \"{beat.intent}\" · clip {index + 1} of {len(beat.clips)}")
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
            out.append("- Dormant: " + ", ".join(f"{e.id} (clip {e.last_seen})" for e in dormant))
        if first and beat.input != "none":
            out.append(f"- Viewer: {beat.input}" + (f" ({beat.input_reason})" if beat.input_reason else ""))
        elif first and world.direction is not None:
            out.append(f"- Viewer direction (clip {world.direction.clip}): \"{world.direction.text}\"")
        spec = beat.clips[index]
        if spec.dialogue:
            out.append("- Dialogue: " + " / ".join(f"{d.speaker}: {d.line}" for d in spec.dialogue))
        if first and meta.get("trigger"):
            out.append(f"- Then spawned: {meta['trigger']}")
        for n in notes:
            out.append(f"- {n}")
        return "\n".join(out) + "\n"
