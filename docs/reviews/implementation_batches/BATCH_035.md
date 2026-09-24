# BATCH_035 — One standard frame size, prepared (RUN-17, D-15)

**Status:** COMPLETED as preparation (the adoption waits for its GPU check on session 4) · **Date:** 2026-09-24 · **Base:** BATCH_034

## Plan and result

D-15: one standard image and video size, 512×288 (exactly 16:9, on LTX's multiples of 32). References are any exact 16:9 size, used without crop or padding; any other aspect is refused. Adopted after a GPU check of picture quality and speed against 640×352. This batch builds what the check and the adoption need. Nothing in the production path changes until then: `server_defaults.sh` still renders 640×352 and the live config still fits with `pad`.

| File | Change |
|---|---|
| `src/i2v/ltxv.py` | `reference_fit: strict` now means the standard's rule. `exact_aspect`: the reference's height is its width at the render aspect, within one pixel (1920×1080 or the owner's 1672×941 at 512×288). Such a reference is resized whole, no crop and no padding. Any other is refused, with the size to supply (e.g. 1920x1080). Before, `strict` accepted anything within 5 % and cover-cropped it; no config used it. The run-start check reports `fit: exact`. |
| `src/models/config.py` | The `i2v_reference_fit` comment. |
| `tests/test_i2v.py` | Under `strict`: 1672×941, 1920×1080 and 768×432 are taken whole at 512×288 (a blue top row survives, where a cover would crop it); 1672×930 and a master 2.8 % off are refused. |
| `scripts/experiments/gpu_session1.py` | An arm may name its render `size` (rendered only on a server at that size, so one command serves both halves of a restart) and its reference `fit`. |
| `scripts/experiments/session4_prepare.py` | GPU session 4's arms. `run17`: session A's segments 1-12 at 640×352 with today's letterboxed square (two seeds), at 512×288 with a 16:9 reference fitted strictly (two seeds), and at 512×288 with the letterboxed square (the size alone). `ltx15`: the render check of BATCH_034 (a scripted four-beat film with three cuts through the production engine and compiler, and the same film without cuts, two seeds). |
| `docs/ARCHITECTURE.md` | The two fits. |

**The candidate reference** (`logs/experiments/run17/legoman-medium-768x432.png`, not committed) is a 768×432 crop of the owner's 16:9 master, centred on the figure. The figure is 75 % of the frame height, the medium shot the opening prompt asks for. X-12's jolt came from the master's wide composition (the figure at 35 %). The owner's master in the working tree is untouched. A story reference at the standard's aspect is needed before `strict` can be the live setting: the committed square would be refused.

## Validation

- `python -m tests.run`: 143 passed.
- On GPU session 4: the run17 arms by eye (identity, framing, detail at 512×288 against 640×352), and `scripts/latency_probe.py --warm 30` at each size.

**Commit:** this batch's.
