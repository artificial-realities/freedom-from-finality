"""Tk popup window — story transcript streamed live, terminal-style.

Used by the no-video live mode (`run_live_text`). The window is a single
scrolling Text widget rendered in a dark monospace style, mirroring the
markdown transcript already written to `logs/`.

Tk must own a main loop on the OS UI thread, but our async runner owns
the main thread. We resolve this by spawning a daemon thread that runs
`Tk.mainloop()` and exposing a thread-safe `append_turn(...)` that the
async loop can call from anywhere — internally it schedules the text
update via `Tk.after(0, ...)`.

The runner builds a `StoryPopup`, calls `start()` (returns once the
window is visible), pushes turns in via `append_turn(...)` while the
story flows, and `stop()`s on shutdown.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Iterable

from src.models.responses import Beat, Commentary, Shot

logger = logging.getLogger(__name__)


class StoryPopup:
    def __init__(self, *, title: str = "ClankerStudios", width: int = 100, height: int = 36) -> None:
        self._title = title
        self._width = width
        self._height = height
        self._ready = threading.Event()
        self._stop = threading.Event()
        # Pending text chunks queued before the Tk thread is up; drained
        # once `_root` exists.
        self._pending: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root = None
        self._text = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        # Wait for the widget to exist so subsequent appends can schedule
        # against it. If Tk fails to start we still return — callers
        # check `is_alive()` if they need to know.
        self._ready.wait(timeout=5.0)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def append_turn(
        self,
        *,
        turn: int,
        user_input: str,
        beat: Beat | None,
        shot: Shot | None,
        commentary: Commentary | None,
        world_state: dict,
        narrative_memory: str,
        context_brief: str,
    ) -> None:
        text = _format_turn(
            turn=turn,
            user_input=user_input,
            beat=beat,
            shot=shot,
            commentary=commentary,
            world_state=world_state,
            narrative_memory=narrative_memory,
            context_brief=context_brief,
        )
        self._append(text)

    def _append(self, text: str) -> None:
        if self._root is None or self._text is None:
            self._pending.put(text)
            return
        try:
            self._root.after(0, self._do_append, text)
        except Exception:
            # Window torn down between liveness check and schedule.
            self._pending.put(text)

    def _do_append(self, text: str) -> None:
        if self._text is None:
            return
        try:
            self._text.configure(state="normal")
            self._text.insert("end", text)
            self._text.see("end")
            self._text.configure(state="disabled")
        except Exception:
            logger.exception("popup append failed")

    def _run_tk(self) -> None:
        try:
            import tkinter as tk
            from tkinter import scrolledtext
        except Exception:
            logger.exception("Tk unavailable — popup will not appear")
            self._ready.set()
            self._stop.set()
            return

        try:
            root = tk.Tk()
            root.title(self._title)
            root.configure(bg="#0b0b0b")
            text = scrolledtext.ScrolledText(
                root,
                width=self._width,
                height=self._height,
                bg="#0b0b0b",
                fg="#e6e6e6",
                insertbackground="#e6e6e6",
                font=("Menlo", 11) if _has_font("Menlo") else ("Courier", 11),
                wrap="word",
                borderwidth=0,
                highlightthickness=0,
                padx=12,
                pady=10,
            )
            text.pack(fill="both", expand=True)
            text.configure(state="disabled")
        except Exception:
            logger.exception("Tk window creation failed")
            self._ready.set()
            self._stop.set()
            return

        self._root = root
        self._text = text

        # Drain anything queued before the widget existed.
        while not self._pending.empty():
            try:
                self._do_append(self._pending.get_nowait())
            except queue.Empty:
                break

        self._ready.set()

        try:
            root.mainloop()
        except Exception:
            logger.exception("Tk mainloop crashed")
        finally:
            self._stop.set()
            self._root = None
            self._text = None


def _has_font(name: str) -> bool:
    try:
        from tkinter import font as tkfont
        return name in tkfont.families()
    except Exception:
        return False


def _format_turn(
    *,
    turn: int,
    user_input: str,
    beat: Beat | None,
    shot: Shot | None,
    commentary: Commentary | None,
    world_state: dict,
    narrative_memory: str,
    context_brief: str,
) -> str:
    rule = "═" * 76
    heading_input = f'"{user_input}"' if user_input.strip() else "(silent)"
    out: list[str] = [
        f"\n{rule}\n",
        f"TURN {turn} — {heading_input}\n",
        f"{rule}\n\n",
    ]

    if beat is None:
        out.append("[BEAT] — no beat (parse failure)\n\n")
    else:
        out.append("[BEAT — Tolkien]\n")
        out.append(f"{beat.narration}\n")
        out.append(f"  Action:  {beat.action}\n")
        out.append(f"  Outcome: {beat.outcome}\n")
        if beat.short_term_narrative:
            out.append(f"  Short-term: {beat.short_term_narrative}\n")
        if beat.long_term_narrative:
            out.append(f"  Long-term:  {beat.long_term_narrative}\n")
        out.append("\n")

    if shot is None:
        out.append("[SHOT] — no shot (parse failure)\n\n")
    else:
        out.append("[SHOT — Spielberg]\n")
        out.append(f"{shot.i2v_prompt}\n")
        if shot.on_screen:
            out.append(f"  On screen: {', '.join(shot.on_screen)}\n")
        out.append(f"  Camera:    {shot.camera}\n")
        out.append(f"  Motion:    {shot.motion}\n")
        out.append(f"  End frame: {shot.end_frame_description}\n")
        out.append("\n")

    if commentary is None:
        out.append("[VOICE-OVER] — no commentary (parse failure)\n\n")
    elif not commentary.voiceover.strip():
        out.append("[VOICE-OVER — Attenborough]\n  (silent)\n\n")
    else:
        out.append("[VOICE-OVER — Attenborough]\n")
        for line in _split_lines(commentary.voiceover):
            out.append(f"  > {line}\n")
        out.append("\n")

    out.append("[MEMORY — Spock]\n")
    inventory = world_state.get("inventory")
    location = world_state.get("protagonist_location")
    characters = world_state.get("characters")
    if inventory is not None:
        rendered = ", ".join(inventory) if inventory else "(empty)"
        out.append(f"  Inventory:  {rendered}\n")
    if location:
        out.append(f"  Location:   {location}\n")
    if characters:
        out.append(f"  Characters: {characters}\n")
    if narrative_memory:
        out.append(f"  Narrative:  {narrative_memory}\n")
    if context_brief:
        out.append(f"  Brief next: {context_brief}\n")
    out.append("\n")

    return "".join(out)


def _split_lines(text: str) -> Iterable[str]:
    lines = text.splitlines()
    return lines if lines else [text]
