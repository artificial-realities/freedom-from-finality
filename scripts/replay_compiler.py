"""Compiler replay harness: a run's Director outputs through the prompt
compiler, offline, so a compiler change (or the LTX-01 experiment arm)
can be compared on the same per-clip fields with the LLM out of the loop
(RUN-13; the input X-05 and X-04 need).

The compiler's input is the Director's `BeatPlan` and the world model as
it stood after the clip's ops were applied. A run's `events.jsonl` knows
which clips of which beat were emitted, their viewer line and whether
the voiceover gate was open, but not the plan itself; Langfuse stores
every Director generation's output verbatim, keyed by the segment it was
requested at. `build` joins the two into one self-contained replay file;
`run` replays it the way the engine emits: a world from the blueprint,
then per clip the plan as the engine normalised it, `apply_clip`, the
compiler. With the current compiler the prompts come out equal to the
transcript's (`--verify`); with another compiler `--diff` shows what
changed.

    python scripts/replay_compiler.py fetch <langfuse session id> --out lf.json
    python scripts/replay_compiler.py build --session logs/live-box/sessions/<id> --langfuse lf.json \\
        --story stories/lego-void --out replay.json
    python scripts/replay_compiler.py run replay.json --out prompts.json --verify logs/live-box/<id>.md
    python scripts/replay_compiler.py run replay.json --compiler src.story.compiler_prose:compile_clip \\
        --out prose.json --diff prompts.json --first 1 --count 12

`--compiler` is `module:function` with `compile_clip`'s signature. The
config gives `narration`, `voiceover_style` and `max_clips_per_beat`.
Beats without a plan (a Director failure's fallback beat, a missing
generation) are listed in the replay file and skipped with a note.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import importlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.models.config import Config  # noqa: E402
from src.models.story import Story  # noqa: E402
from src.story.schemas import BeatPlan  # noqa: E402
from src.story.validator import apply_clip  # noqa: E402
from src.story.world import WorldModel  # noqa: E402

DEFAULT_COMPILER = "src.story.compiler:compile_clip"
DEFAULT_CONFIG = REPO / "configs" / "director.live.yaml"
REPLAY_SCHEMA = "replay/1"
PROMPTS_SCHEMA = "prompts/1"


# ── Sources ──────────────────────────────────────────────────────────

def read_events(session_dir: Path) -> list[dict]:
    path = Path(session_dir) / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def plans_from_langfuse(export: dict) -> dict[int, dict]:
    """Segment → the Director's `BeatPlan` (as a dict) from a Langfuse
    export in `fetch`'s format (traces and observations). The last
    generation at a segment wins (a re-ask or a restarted call follows
    the one it replaces)."""
    plans: dict[int, dict] = {}
    for obs in sorted(export.get("observations", []), key=lambda o: str(o.get("startTime", ""))):
        if obs.get("name") != "director" or obs.get("type") not in (None, "GENERATION"):
            continue
        meta = obs.get("metadata") or {}
        turn = meta.get("turn")
        output = obs.get("output")
        content = output.get("content") if isinstance(output, dict) else output
        if turn is None or not content:
            continue
        try:
            plan = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            continue
        if isinstance(plan, dict) and "clips" in plan:
            plans[int(turn)] = plan
    return plans


def build_replay(events: list[dict], plans_by_segment: dict[int, dict], *, story: str, session: str,
                 source: dict | None = None) -> dict:
    """The replay file: each beat's plan keyed by beat number (from the
    generation at the beat's first segment) and the emitted clips in
    order, with what the engine read at emit time."""
    beats: dict[str, dict] = {}
    missing: list[dict] = []
    clips: list[dict] = []
    for ev in sorted(events, key=lambda e: e["segment"]):
        beat = int(ev.get("beat", 0))
        if ev.get("clip_index", 1) == 1 and str(beat) not in beats and not any(m["beat"] == beat for m in missing):
            plan = plans_by_segment.get(ev["segment"])
            if plan is None:
                missing.append({"beat": beat, "segment": ev["segment"], "intent": ev.get("beat_intent", "")})
            else:
                entry = {"segment": ev["segment"], "intent": ev.get("beat_intent", ""), "plan": plan}
                if plan.get("intent", "") != ev.get("beat_intent", ""):
                    entry["note"] = "the event's intent differs from the plan's"
                beats[str(beat)] = entry
        clips.append({
            "segment": ev["segment"], "beat": beat, "clip_index": ev.get("clip_index", 1),
            "clip_count": ev.get("clip_count", 1), "user_input": ev.get("user_input", ""),
            "intervention": ev.get("intervention", "none"),
            "voiceover_kept": bool(ev.get("voiceover")) or ev.get("clip_index", 1) != 1,
            "duration": ev.get("duration", 0),
        })
    return {"schema": REPLAY_SCHEMA, "session": session, "story": story, "source": source or {},
            "beats": beats, "missing_beats": missing, "clips": clips}


# ── Langfuse fetch (read-only) ───────────────────────────────────────

def _langfuse_env() -> tuple[str, str, str]:
    env = dict(os.environ)
    dotenv = REPO / ".env"
    if dotenv.exists():
        try:
            from dotenv import dotenv_values
            env = {**{k: v for k, v in dotenv_values(dotenv).items() if v is not None}, **env}
        except ImportError:
            pass
    host = env.get("LANGFUSE_HOST") or "https://cloud.langfuse.com"
    public, secret = env.get("LANGFUSE_PUBLIC_KEY", ""), env.get("LANGFUSE_SECRET_KEY", "")
    if not public or not secret:
        raise SystemExit("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are needed (environment or .env)")
    return host, public, secret


def fetch_langfuse(session_id: str) -> dict:
    """Every trace of the session and the observations inside them,
    through the public REST API; rate limits are waited out."""
    host, public, secret = _langfuse_env()
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()

    def get(path: str, **params) -> dict:
        url = host.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(8):
            req = urllib.request.Request(url, headers={"Authorization": "Basic " + auth})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise RuntimeError("Langfuse: rate limited eight times")

    traces: list[dict] = []
    page = 1
    while True:
        d = get("/api/public/traces", sessionId=session_id, limit=100, page=page)
        traces += d["data"]
        if page >= d.get("meta", {}).get("totalPages", 1):
            break
        page += 1
    if not traces:
        return {"traces": [], "observations": []}
    from datetime import datetime, timedelta
    stamps = sorted(t["timestamp"] for t in traces)
    t0 = datetime.fromisoformat(stamps[0].replace("Z", "+00:00")) - timedelta(minutes=2)
    t1 = datetime.fromisoformat(stamps[-1].replace("Z", "+00:00")) + timedelta(minutes=5)
    ids = {t["id"] for t in traces}
    observations: list[dict] = []
    page = 1
    while True:
        d = get("/api/public/observations", fromStartTime=t0.isoformat().replace("+00:00", "Z"),
                toStartTime=t1.isoformat().replace("+00:00", "Z"), limit=100, page=page)
        observations += [o for o in d["data"] if o.get("traceId") in ids]
        if page >= d.get("meta", {}).get("totalPages", 1):
            break
        page += 1
        time.sleep(0.5)
    return {"traces": traces, "observations": observations}


# ── Replay ───────────────────────────────────────────────────────────

def load_compiler(spec: str):
    module, _, func = spec.partition(":")
    if not module or not func:
        raise ValueError(f"--compiler wants module:function, got {spec!r}")
    return getattr(importlib.import_module(module), func)


def replay(data: dict, *, compiler, config: Config, story: Story) -> list[dict]:
    """The compiled prompt of every emitted clip, in order, from the
    blueprint world forward. A clip whose beat has no plan, or that lies
    beyond its plan, gets a note and no prompt."""
    world = WorldModel.from_blueprint(title=story.title, characters=story.characters,
                                      locations=story.locations, cast_locked=story.cast_locked)
    plans: dict[int, BeatPlan | None] = {}
    rows: list[dict] = []
    for clip in data["clips"]:
        beat, index, seg = int(clip["beat"]), int(clip["clip_index"]) - 1, int(clip["segment"])
        row = {"segment": seg, "beat": beat, "clip_index": index + 1, "opening": seg == 1}
        if index == 0:
            entry = data["beats"].get(str(beat))
            if entry is None:
                plans[beat] = None
            else:
                plan = BeatPlan(**entry["plan"])
                plan.clips = plan.clips[: config.max_clips_per_beat]
                if not clip.get("voiceover_kept", True):
                    plan.voiceover = ""
                plans[beat] = plan
            world.segment = seg
            if clip.get("user_input"):
                world.add_intervention(seg, clip["user_input"])
        plan = plans.get(beat)
        if plan is None:
            row["note"] = "no plan for this beat"
            rows.append(row)
            continue
        if index >= len(plan.clips):
            row["note"] = f"clip {index + 1} lies beyond the plan's {len(plan.clips)} clips"
            rows.append(row)
            continue
        world.segment = seg
        apply_clip(plan, index, world, segment_no=seg)
        if index == 0 and plan.intervention == "absorbed" and clip.get("user_input"):
            world.viewer_momentum = clip["user_input"]
            world.viewer_momentum_segment = seg
        prompt = compiler(
            plan, index, world,
            visual_style=story.visual_style,
            visual_anchor=story.visual_anchor,
            narration_mode=config.narration,
            voiceover_style=config.voiceover_style,
            opening=(seg == 1),
            segment_no=seg,
        )
        row.update({"prompt": prompt, "words": len(prompt.split()), "chars": len(prompt)})
        rows.append(row)
    return rows


def word_stats(rows: list[dict]) -> dict:
    compiled = [r for r in rows if "prompt" in r]
    opening = [r["words"] for r in compiled if r["opening"]]
    continuing = [r["words"] for r in compiled if not r["opening"]]
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return {"clips": len(rows), "compiled": len(compiled), "skipped": len(rows) - len(compiled),
            "words_mean": mean([r["words"] for r in compiled]), "words_opening": mean(opening),
            "words_continuing": mean(continuing),
            "words_max": max((r["words"] for r in compiled), default=None)}


# ── Verify and diff ──────────────────────────────────────────────────

_TURN = re.compile(r"^## Turn (\d+) ", re.M)


def transcript_prompts(path: Path) -> dict[int, str]:
    """Segment → the Shot prompt of the markdown transcript."""
    text = Path(path).read_text(encoding="utf-8")
    heads = list(_TURN.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(heads):
        section = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        shot = section.find("**Shot**\n\n")
        if shot < 0:
            continue
        body = section[shot + len("**Shot**\n\n"):]
        end = body.find("\n\n")
        out[int(m.group(1))] = (body if end < 0 else body[:end]).strip()
    return out


def verify(rows: list[dict], transcript: dict[int, str]) -> list[dict]:
    """Rows whose prompt differs from the transcript's, or that one side lacks."""
    mismatches = []
    for r in rows:
        expected = transcript.get(r["segment"])
        got = r.get("prompt")
        if expected is None:
            mismatches.append({"segment": r["segment"], "reason": "not in the transcript"})
        elif got is None:
            mismatches.append({"segment": r["segment"], "reason": r.get("note", "not compiled")})
        elif got != expected:
            mismatches.append({"segment": r["segment"], "reason": "differs", "diff": _diff_text(expected, got)})
    return mismatches


def _diff_text(a: str, b: str, *, a_name: str = "transcript", b_name: str = "replay") -> str:
    return "\n".join(difflib.unified_diff(a.split(" "), b.split(" "), a_name, b_name, lineterm="", n=2))


def diff_prompts(ours: list[dict], theirs: list[dict], *, limit: int = 3) -> dict:
    """Word counts side by side and unified word diffs of the first
    `limit` clips that differ."""
    other = {r["segment"]: r for r in theirs}
    per_clip, diffs = [], []
    for r in ours:
        o = other.get(r["segment"])
        if o is None or "prompt" not in r or "prompt" not in o:
            continue
        same = r["prompt"] == o["prompt"]
        per_clip.append({"segment": r["segment"], "words": r["words"], "other_words": o["words"], "same": same})
        if not same and len(diffs) < limit:
            diffs.append({"segment": r["segment"], "diff": _diff_text(o["prompt"], r["prompt"], a_name="other", b_name="this")})
    compared = len(per_clip)
    return {"compared": compared, "identical": sum(1 for p in per_clip if p["same"]),
            "words_mean": round(sum(p["words"] for p in per_clip) / compared, 1) if compared else None,
            "other_words_mean": round(sum(p["other_words"] for p in per_clip) / compared, 1) if compared else None,
            "per_clip": per_clip, "diffs": diffs}


# ── Main ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="a session's Langfuse traces and observations, to a JSON file")
    f.add_argument("session_id")
    f.add_argument("--out", type=Path, required=True)

    b = sub.add_parser("build", help="a replay file from a session directory and a Langfuse export")
    b.add_argument("--session", type=Path, required=True, help="logs/.../sessions/<id> (events.jsonl)")
    b.add_argument("--langfuse", type=Path, required=True, help="the export written by fetch")
    b.add_argument("--story", default="stories/lego-void")
    b.add_argument("--out", type=Path, required=True)

    r = sub.add_parser("run", help="replay a replay file through a compiler")
    r.add_argument("replay", type=Path)
    r.add_argument("--compiler", default=DEFAULT_COMPILER, help="module:function (default: the current compiler)")
    r.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    r.add_argument("--story", default=None, help="overrides the replay file's story path")
    r.add_argument("--first", type=int, default=None, help="first segment to output (the world is always replayed from 1)")
    r.add_argument("--count", type=int, default=None, help="how many segments to output")
    r.add_argument("--out", type=Path, default=None, help="prompts JSON (default: print a summary only)")
    r.add_argument("--verify", type=Path, default=None, help="a markdown transcript to compare with")
    r.add_argument("--diff", type=Path, default=None, help="another run's prompts JSON to compare with")
    r.add_argument("--show", type=int, default=0, help="print this many prompts")
    args = ap.parse_args(argv)

    if args.command == "fetch":
        export = fetch_langfuse(args.session_id)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(export, default=str))
        print(f"{len(export['traces'])} traces, {len(export['observations'])} observations → {args.out}")
        return 0

    if args.command == "build":
        events = read_events(args.session)
        export = json.loads(args.langfuse.read_text(encoding="utf-8"))
        plans = plans_from_langfuse(export)
        data = build_replay(events, plans, story=args.story, session=args.session.name,
                            source={"session": str(args.session), "langfuse": str(args.langfuse),
                                    "generations": len(plans)})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(f"{len(data['clips'])} clips, {len(data['beats'])} beats with a plan, "
              f"{len(data['missing_beats'])} without → {args.out}")
        for m in data["missing_beats"]:
            print(f"  beat {m['beat']} at segment {m['segment']} has no plan: {m['intent'][:60]}")
        return 0

    data = json.loads(args.replay.read_text(encoding="utf-8"))
    if data.get("schema") != REPLAY_SCHEMA:
        print(f"{args.replay} is not a {REPLAY_SCHEMA} file", file=sys.stderr)
        return 2
    story = Story.from_json(args.story or data["story"])
    config = Config.from_yaml(args.config)
    compiler = load_compiler(args.compiler)
    rows = replay(data, compiler=compiler, config=config, story=story)
    if args.first is not None:
        rows = [r for r in rows if r["segment"] >= args.first]
    if args.count is not None:
        rows = rows[: args.count]
    stats = word_stats(rows)
    result = {"schema": PROMPTS_SCHEMA, "replay": str(args.replay), "session": data.get("session"),
              "compiler": args.compiler, "config": str(args.config), "story": args.story or data["story"],
              "stats": stats, "clips": rows}
    status = 0
    print(f"{stats['compiled']} of {stats['clips']} clips compiled with {args.compiler}: "
          f"{stats['words_mean']} words mean (opening {stats['words_opening']}, continuing {stats['words_continuing']}, "
          f"max {stats['words_max']})")
    for r in rows[: args.show]:
        print(f"\n[{r['segment']}] {r.get('prompt', r.get('note'))}")
    if args.verify:
        mismatches = verify(rows, transcript_prompts(args.verify))
        result["verify"] = {"transcript": str(args.verify), "mismatches": mismatches}
        print(f"verify against {args.verify.name}: {len(rows) - len(mismatches)} of {len(rows)} prompts equal"
              + (f"; first mismatch at segment {mismatches[0]['segment']} ({mismatches[0]['reason']})" if mismatches else ""))
        if mismatches and mismatches[0].get("diff"):
            print(mismatches[0]["diff"])
        status = 1 if mismatches else status
    if args.diff:
        other = json.loads(args.diff.read_text(encoding="utf-8"))
        d = diff_prompts(rows, other.get("clips", []))
        result["diff"] = {"other": str(args.diff), "other_compiler": other.get("compiler"), **d}
        print(f"diff against {args.diff.name} ({other.get('compiler')}): {d['identical']} of {d['compared']} identical; "
              f"words mean {d['words_mean']} against {d['other_words_mean']}")
        for item in d["diffs"]:
            print(f"\n[{item['segment']}]\n{item['diff']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
        print(f"written to {args.out}")
    return status


if __name__ == "__main__":
    sys.exit(main())
