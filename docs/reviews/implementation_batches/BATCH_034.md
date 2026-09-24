# BATCH_034 — Multi-shot as a Director option (LTX-15, D-13)

**Status:** COMPLETED headless · the render check waits for GPU session 4 · **Date:** 2026-09-24 · **Base:** BATCH_033

## Plan and result

D-13: add multi-shot as an explicit per-clip option of the Director, test it headless, and render a check on the next GPU session. X-31 had shown cuts written in prose are controllable on LTX-2.5 (6 of 6 written cuts rendered, 0 of 12 unwritten). Cuts are a story's choice, like its perspective: the Lego story stays one continuous take until the render check.

| File | Change |
|---|---|
| `src/story/schemas.py` | `ClipSpec.cut_to` ("" = the clip continues the shot), the clip's first field, so the Director decides the shot before writing the action in it. `action_text()` reads "Cut to … ." first, so the transcript, the player and the Showrunner see the cut. Strict subset kept (a plain string). |
| `src/models/story.py` | `Perspective.cuts` (default false) and `Perspective.cutting` (the story's guidance on when). |
| `src/story/world.py` | `WorldModel.shot`: the shot the picture is in. |
| `src/story/validator.py` | `apply_clip` drops a cut with a warning when the story does not allow cuts, on the film's first clip (it opens from the reference), or when it names the shot the clip already opens on. It then sets the world's shot from the beat's framing and the clip's cut. |
| `src/story/compiler.py` | `shot_before(beat, index)`. A cut clip opens on that shot ("Medium shot, straight-on, eye level."), then "The view cuts to a close-up of …; the camera remains static." before the action (X-31's convention, an article added when missing). The clips after it open on the new shot. A beat without cuts compiles byte-identically (the session A replay test is unchanged). |
| `src/prompts/director.system.md`, `src/story/context.py`, `src/agents/director.py` | The `cut_to` field text. `{cut_rule}` replaces the fixed "The picture never cuts" line: `format_cut_rule` gives that line plus "`cut_to` stays empty" for a continuous story, or the cut rule with the story's `cutting` guidance. |
| `src/graph/director_engine.py` | Where cuts are allowed, the picture section ends "The picture is in this shot: …". `current_shot.camera` is the current shot. |
| `tests/test_multishot.py` | 5 tests: the compiled cut and the clip after it; determiners; the story's permission, the first clip, the same shot; the rule text; the engine end to end (cut in the prompt, the shot in the next plan, nothing for a continuous story). |
| `docs/ARCHITECTURE.md`, `CLAUDE.md` | Cuts as the story's choice. |

## Headless readings (the real Director, gpt-4.1 priority, the Lego story with cuts allowed)

- **First wording** ("empty, the normal choice"; "a cut is a choice, not a rhythm … rarely more than one per beat"): **0 cuts in 44 clips**, and 0 in 44 with story guidance asking for about one cut every half minute (`logs/experiments/ltx15/runs*/`).
- **An offline probe** (one world, ten calls) showed what the Director did with the field when it did fill it: it restated the framing ("medium shot, straight-on, eye level", 6 of 6 times). That is not a cut. The validator now drops a cut to the shot the clip already opens on.
- **The final wording** says the field is empty when the clip continues the shot, and otherwise names a different view that shows the moment better, never the current framing again. The rule leads with "besides camera moves, a clip may cut". With the guidance: **6 cuts in 43 clips**, one per 43 s of film against the guidance's "about one every half minute" (`logs/experiments/ltx15/runs-guided2/`):
  - in to a close-up as the paper boat arrives, and back out (segments 6-7);
  - in to the boat on his head as a bounce launches it, and back out (19-22);
  - two changes of angle (28, 38).

  Each following beat opened on the shot it was told. The validator dropped none. The Director averaged 3.72 s against 3.29-3.35 s in the runs without cuts (one run each: not a latency reading).
- **The Lego story keeps `cuts: false`** until the render check. `logs/experiments/ltx15/lego-cuts-guided/story.json` has the guidance used here, a candidate for the story.

## Validation

- `python -m tests.run`: 143 passed (5 new).
- The render check (a scripted film through the production engine and compiler, with a no-cut control, two seeds) is prepared for GPU session 4: `scripts/experiments/session4_prepare.py`, arms `ltx15/cut-s*` and `ltx15/plain-s*`.

**Commit:** this batch's.
