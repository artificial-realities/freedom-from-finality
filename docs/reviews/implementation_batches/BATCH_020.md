# BATCH_020 — X-26's LLM half, GPU session 1 prepared, and a rental that could not be reached

**Status:** BLOCKED (infrastructure) · **Date:** 2026-09-23 · **Base:** `63a074e` (committed as `edeb6f4` before the rental)

## What was done

- **X-26, LLM half** (`docs/reviews/experiments/X-26.md`): the lighthouse story run with the real Director. A new character is registered once with role, voice, look and state; the permitted first-person switch is used; 2 re-asks in 24 calls, none about the policy; a must-appear supporting character did not enter within two minutes of its condition; the "forbidden" request was read as a camera height, which the policy allows. A generated short look no longer ends in a comma.
- **GPU session 1 prepared** (`scripts/experiments/gpu_session1.py`): 240 clips in 18 arms for X-03, X-04, X-05, X-06, LTX-09's rerun and an A/V-alignment arm for X-03 under AUD-03's ceiling; prompt chains from session A's replay file through both compilers and X-06's forms; derived seeds shared across each experiment's arms; resumable rendering with every request, header and clip kept; analysis of render ratios, seam jumps (with the overlap hidden for 153 frames), figure boxes and colour shares, loudness, A/V length, rerun equality in bytes and pixels, and contact sheets. Smoke-tested end to end against the suite's in-process fake LTX server. Inputs in `logs/experiments/gpu1/` (`plan.json`, `*/arm.json`).
- **The rental.** Vast offer 52263602 (H100 NVL, $3.07/h) was rented at 17:23:49 UTC as instance 52268035 with `LTX2_WARMUP_FRAMES=145,145,145,153,153,153`. The instance reached `running`, but neither its direct ssh port (20.157.124.84:40817) nor Vast's ssh proxy (ssh9.vast.ai:28034) could be reached from this machine, with or without the command sandbox: outbound connections to non-standard ports are blocked from this network (a test against portquiz.net on the same port fails; ports 22 and 443 to other hosts work). The earlier live runs reached Vast from a network that allowed it. Following the owner's rule (stop after infrastructure failures), the instance was destroyed at about 17:36 UTC without the server being installed: about 13 minutes, **≈ $0.68**. No instance is left running (`vastai show instances` is empty).

## What the next GPU session needs

A machine whose network allows outbound ssh to Vast's high ports (or a Vast image with a reachable port). Then: `LTX2_WARMUP_FRAMES=145,145,145,153,153,153 server/vast_run.sh up`, `python scripts/experiments/gpu_session1.py run`, `vast_run.sh restart` with `LTX2_AUDIO_CEILING_LUFS=-18` and `run --only x03av`, optionally `scripts/latency_probe.py --warm 30`, `vast_run.sh down`, then `gpu_session1.py analyse` locally. Expected about 45-60 minutes of rendering plus setup, well inside the remaining budget.

## Validation

- `python -m tests.run`: 114 passed (the only code change is the short-look fix, covered by `tests/test_cast_policy.py`).
