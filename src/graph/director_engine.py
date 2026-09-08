"""Director engine (`graph: "director"`).

Two LLM roles on two clocks, everything else deterministic:

- **Director** — one structured call per segment on the critical path.
  Reads the assembled context, returns a `Segment` (narration, shots,
  audio, world delta, thread ops, viewer disposition, flags).
- **Showrunner** — an async task on a slow clock, spawned on triggers
  (bootstrap, scene exit, stagnation, a replan flag, or every
  `compaction_interval` segments) and joined at the start of the next
  segment. Compacts the event log and writes the plan.

Code owns the rest: the typed `WorldModel`, validation and apply, the
prompt compiler, the pacing gates, and persistence per segment.

For the runner the engine keeps the legacy `StoryState` surface: it
fills `current_beat`, `current_shot`, `current_commentary` as views of
the segment, so rendering, playback, history and the transcript work
unchanged for either engine.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.agents import director as director_agent
from src.agents import showrunner as showrunner_agent
from src.llm import build_backend
from src.llm.base import LLMBackend
from src.models.config import Config
from src.models.responses import Beat, Commentary, Shot
from src.state.story_state import StoryState
from src.story.compiler import compile_prompt
from src.story.pacing import pacing_note, scene_note, tick_story_clock, voiceover_gate
from src.story.schemas import Segment
from src.story.validator import Report, apply, apply_showrunner
from src.story.world import EventRecord, WorldModel
from src.tts.elevenlabs import ElevenLabsTTS
from src.util.langfuse_setup import event as langfuse_event

logger = logging.getLogger(__name__)


class DirectorEngine:
    def __init__(
        self,
        *,
        llm: LLMBackend,
        config: Config,
        tts: ElevenLabsTTS | None = None,
        session_dir: Path | str | None = None,
    ) -> None:
        self.llm = llm
        self.config = config
        self.tts = tts
        self.session_dir = Path(session_dir) if session_dir else None
        self.world: WorldModel | None = None
        self.llm_director = (
            build_backend(config.llm_backend, config.director_model)
            if config.director_model else llm
        )
        self.llm_showrunner = (
            build_backend(config.llm_backend, config.showrunner_model)
            if config.showrunner_model else llm
        )
        self._showrunner_task: asyncio.Task | None = None
        self._showrunner_meta: tuple[int, bool, str] | None = None  # (at_segment, force_replan, reason)
        self._showrunner_notes: list[str] = []

    # ── Turn ────────────────────────────────────────────────────────

    async def ainvoke(self, state: StoryState) -> StoryState:
        if self.world is None:
            self.world = self._init_world(state)
        world = self.world

        await self._join_showrunner()
        notes: list[str] = list(self._showrunner_notes)
        self._showrunner_notes = []

        seg = state.turn_number
        world.segment = seg
        text = (state.user_input or "").strip()
        if text:
            world.add_intervention(seg, text)

        if world.plan.scene is None:
            await self._run_showrunner_now(state, reason="bootstrap: opening the film", mode="bootstrap", at_segment=seg - 1)
            notes.extend(self._showrunner_notes)
            self._showrunner_notes = []

        vo_open = voiceover_gate(state, self.config)
        pnote = pacing_note(world, self.config)
        snote = scene_note(world, self.config)

        segment, dnotes = await director_agent.run(
            world=world, state=state, llm=self.llm_director, config=self.config,
            voiceover_open=vo_open, pacing_note=pnote, scene_note=snote,
        )
        notes.extend(dnotes)

        if segment is None:
            state.current_beat = None
            state.current_shot = None
            state.current_commentary = None
            state.current_audio_path = ""
            state.turn_notes = "**World**\n\n- Director call failed; no segment this turn.\n"
            self._tick(state)
            return state

        report = apply(segment, world, segment_no=seg, max_shots=self.config.max_shots_per_segment)
        if not vo_open:
            segment.voiceover = ""

        prompt, duration = compile_prompt(
            segment, world,
            visual_style=state.visual_style,
            narration_mode=self.config.narration,
            voiceover_style=self.config.voiceover_style,
        )
        self._write_views(state, segment, prompt, duration)

        audio_path = ""
        if (
            segment.voiceover
            and self.config.narration == "tts"
            and self.config.audio_enabled
            and self.tts is not None
        ):
            result = await self.tts.synthesize(segment.voiceover, turn=seg)
            langfuse_event(
                "tts", input=segment.voiceover, output=result,
                metadata={"turn": seg, "success": result is not None},
                level="DEFAULT" if result else "WARNING",
            )
            audio_path = result or ""
        state.current_audio_path = audio_path

        forced_done = False
        if not segment.scene_done and world.scene_length() >= self.config.max_segments_per_scene:
            segment.scene_done = True
            forced_done = True

        card = world.plan.scene
        world.events.append(EventRecord(
            segment=seg,
            scene_id=card.id if card else 0,
            user_input=text,
            intervention=segment.intervention,
            narration=segment.narration,
            outcome=segment.outcome,
            next_direction=segment.next_direction,
            applied_ops=list(report.applied),
            thread_ops=[a for a in report.applied if a.startswith("thread ")],
            on_screen=list(segment.shots[-1].on_screen) if segment.shots else [],
            transition=segment.shots[0].transition if segment.shots else "continue",
            end_state=segment.shots[-1].end_state if segment.shots else "",
            voiceover=segment.voiceover,
            duration=duration,
        ))
        self._save()

        reason, mode = self._trigger(segment, forced_done)
        if reason:
            self._spawn_showrunner(state, reason=reason, mode=mode, at_segment=seg)

        state.turn_notes = self._format_notes(
            segment, report, notes, vo_open=vo_open, pacing=pnote, trigger=reason, forced=forced_done,
        )
        self._tick(state)
        return state

    async def drain(self, state: StoryState) -> None:
        await self._join_showrunner()
        self._save()

    # ── Setup ───────────────────────────────────────────────────────

    def _init_world(self, state: StoryState) -> WorldModel:
        if self.session_dir and (self.session_dir / "world.json").exists():
            world = WorldModel.load(self.session_dir)
            logger.info("Resumed world model from %s at segment %d", self.session_dir, world.segment)
            return world
        return WorldModel.from_blueprint(
            title=state.title,
            characters=state.characters,
            locations=state.locations,
            cast_locked=state.cast_locked,
        )

    def _save(self) -> None:
        if self.world is not None and self.session_dir is not None:
            try:
                self.world.save(self.session_dir)
            except OSError:
                logger.exception("Could not persist world model to %s", self.session_dir)

    # ── Showrunner clock ───────────────────────────────────────────

    def _trigger(self, segment: Segment, forced_done: bool) -> tuple[str | None, str]:
        world = self.world
        assert world is not None
        cfg = self.config
        if segment.scene_done:
            return ("scene_done" + (" (scene cap)" if forced_done else ""), "replan")
        if segment.needs_replan:
            return ("needs_replan", "replan")
        t = max(1, cfg.stagnation_threshold)
        if world.stagnation() >= 2 * t and world.segment - world.last_showrunner >= t:
            return (f"stagnation: {world.stagnation()} segments without change", "replan")
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

    async def _run_showrunner_now(self, state: StoryState, *, reason: str, mode: str, at_segment: int) -> None:
        assert self.world is not None
        update = await showrunner_agent.run(
            world=self.world, state=state, llm=self.llm_showrunner,
            config=self.config, reason=reason, mode=mode,
        )
        self._land(update, at_segment=at_segment, force_replan=True, reason=reason)

    async def _join_showrunner(self) -> None:
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

    def _land(self, update, *, at_segment: int, force_replan: bool, reason: str) -> None:
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
        card = self.world.plan.scene
        line = f"showrunner ({reason}): " + ("; ".join(landed) if landed else "nothing to land")
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

    def _write_views(self, state: StoryState, segment: Segment, prompt: str, duration: int) -> None:
        world = self.world
        assert world is not None
        first = segment.shots[0]
        last = segment.shots[-1]
        state.current_beat = Beat(
            narration=segment.narration,
            action=segment.action_text() or segment.outcome,
            outcome=segment.outcome,
            short_term_narrative=segment.next_direction,
            long_term_narrative=None,
        )
        state.short_term_narrative = segment.next_direction or state.short_term_narrative
        on_screen_names = []
        for eid in last.on_screen:
            ent = world.resolve(eid)
            on_screen_names.append(ent.name if ent else eid)
        state.current_shot = Shot(
            i2v_prompt=prompt,
            on_screen=on_screen_names,
            camera=first.framing + (f" · {len(segment.shots)} shots" if len(segment.shots) > 1 else ""),
            motion=first.camera_motion,
            end_frame_description=last.end_state,
            duration_seconds=duration,
        )
        state.current_transition = first.transition
        state.current_commentary = Commentary(voiceover=segment.voiceover)
        here = world.current_location()
        state.world_state = {
            "protagonist_location": here.id if here else "",
            "inventory": [p.name for p in world.held_props()],
            "present": [e.id for e in world.present_entities()],
        }
        card = world.plan.scene
        state.context_brief = f"scene {card.id}: {card.goal}" if card else ""

    def _format_notes(
        self, segment: Segment, report: Report, notes: list[str], *,
        vo_open: bool, pacing: str, trigger: str | None, forced: bool,
    ) -> str:
        world = self.world
        assert world is not None
        card = world.plan.scene
        out = ["**World**\n"]
        if card is not None:
            out.append(f"- Scene {card.id} · segment {world.scene_length()} · goal: {card.goal}")
        if report.applied:
            out.append("- Applied: " + "; ".join(report.applied))
        else:
            out.append("- Applied: (no world change)")
        if report.warnings:
            out.append("- Warnings: " + "; ".join(report.warnings))
        if report.violations:
            out.append("- Rejected: " + "; ".join(report.violations))
        present = ", ".join(e.line() for e in world.present_entities()) or "(nothing)"
        out.append(f"- Present: {present}")
        dormant = world.dormant_props()
        if dormant:
            out.append("- Dormant: " + ", ".join(f"{e.id} (seg {e.last_seen})" for e in dormant))
        threads = world.open_threads()
        if threads:
            out.append("- Open threads: " + "; ".join(f"{t.id} {t.text}" for t in threads[-6:]))
        if segment.intervention != "none":
            out.append(f"- Viewer: {segment.intervention}" + (f" ({segment.intervention_reason})" if segment.intervention_reason else ""))
        out.append(f"- Voice-over floor: {'open' if vo_open else 'closed'} · pacing: {pacing}")
        if len(segment.shots) > 1:
            out.append(f"- Shots: {len(segment.shots)} (" + ", ".join(s.transition for s in segment.shots) + ")")
        elif segment.shots and segment.shots[0].transition == "cut":
            out.append("- Transition: cut (renderer re-seeds from the reference)")
        if segment.dialogue:
            out.append("- Dialogue: " + " / ".join(f"{d.speaker}: {d.line}" for d in segment.dialogue))
        flags = [f for f, v in (("scene_done", segment.scene_done), ("needs_replan", segment.needs_replan)) if v]
        if flags:
            out.append("- Flags: " + ", ".join(flags) + (" (forced by scene cap)" if forced else ""))
        if trigger:
            out.append(f"- Showrunner spawned: {trigger}")
        for n in notes:
            out.append(f"- {n}")
        return "\n".join(out) + "\n"

    def _tick(self, state: StoryState) -> None:
        if not state.pacing_managed:
            tick_story_clock(state, self.config)


def build_director_engine(
    *, llm: LLMBackend, config: Config, tts: ElevenLabsTTS | None = None,
    session_dir: Path | str | None = None,
) -> DirectorEngine:
    return DirectorEngine(llm=llm, config=config, tts=tts, session_dir=session_dir)
