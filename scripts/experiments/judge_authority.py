"""Judge and measure the X-07 / X-08 runs of `story_authority.py`.

For every beat of every arm, an LLM judge (temperature 0, a fixed prompt,
structured output) reads the viewer's latest direction, the scene card in
force and what the beat did, and labels it:

    direction   develops the viewer's latest direction or its consequences
    card        pursues the scene card (its goal or a candidate beat) instead
    both        does both
    other       neither: a free continuation or a new idea

From the labels and the runs' own records it measures, per arm:

- snap-back: after each line, the beats (and story seconds) until the first
  beat labelled `card` before the next line; "held" when none;
- how long the direction holds: story seconds from the answering beat to
  the first beat that is neither `direction` nor `both`;
- movement while the viewer is silent: structural world changes per
  minute, the Director's REQUIRED pacing notes, Showrunner stagnation
  replans;
- the Director's latency on calls that answer a line and on the others, the
  clip counts of answer beats and the beat-length distribution (the
  readings ARCH-11 and ARCH-10 ask for).

    python scripts/experiments/judge_authority.py logs/experiments/story-authority

Writes `judgments.json` (every label with the judge's reason) and
`summary.json`, and prints the table.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

JUDGE_MODEL = "gpt-4.1"
LABELS = ("direction", "card", "both", "other")

JUDGE_SYSTEM = """You read one beat of an interactive, AI-generated film and say what it serves.

The viewer steers the film with typed directions. A planner keeps a scene card: a goal and a few candidate beats for the stretches when the viewer is quiet. You are told the viewer's latest direction (if any), the scene card in force, and what the beat shows.

Label the beat:
- "direction": it develops the viewer's latest direction or its consequences (it may bring in a new element, as long as the direction is what drives it).
- "card": it pursues the scene card's goal or one of its candidate beats instead of the direction.
- "both": it clearly does both at once.
- "other": neither: a continuation of something else, or a new idea from nowhere.

When there is no viewer direction yet, choose between "card" and "other".
Answer with JSON: {"label": ..., "candidate": the 1-based number of the candidate beat it follows or 0, "why": one short sentence}."""

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "candidate": {"type": "integer"},
        "why": {"type": "string"},
    },
    "required": ["label", "candidate", "why"],
    "additionalProperties": False,
}

STRUCTURAL = ("introduce ", "remove ", "holder=", "location=", "present=", "thread ")


def _load_arm(folder: Path) -> dict:
    session = next((folder / "sessions").iterdir())
    events = [json.loads(x) for x in (session / "events.jsonl").read_text().splitlines() if x.strip()]
    timing = [json.loads(x) for x in (session / "timing.jsonl").read_text().splitlines() if x.strip()]
    plans = [json.loads(x) for x in (folder / "plans.jsonl").read_text().splitlines() if x.strip()]
    run = json.loads((folder / "run.json").read_text())
    transcript = next(folder.glob("*.md")).read_text()
    return {"events": events, "timing": timing, "plans": plans, "run": run, "transcript": transcript}


def _beats(arm: dict) -> list[dict]:
    """The beats that played at least one clip, with the plan that made
    them and the viewer direction active when they were planned."""
    by_beat: dict[int, list[dict]] = {}
    for ev in arm["events"]:
        by_beat.setdefault(ev["beat"], []).append(ev)
    out = []
    direction = None
    for index, plan in enumerate(arm["plans"], start=1):
        if plan["answers"]:
            direction = {"text": plan["answers"], "segment": plan["segment"]}
        clips = by_beat.get(index)
        if not clips or plan["beat"] is None:
            continue
        out.append({
            "beat": index, "segment": clips[0]["segment"], "clips": len(clips), "planned_clips": len(plan["beat"]["clips"]),
            "intent": plan["beat"]["intent"], "actions": [c["narration"] for c in clips],
            "answers": plan["answers"], "direction": dict(direction) if direction else None,
            "card": plan["card"], "seconds": sum(c["duration"] for c in clips),
            "story_second": sum(e["duration"] for e in arm["events"] if e["segment"] < clips[0]["segment"]),
            "structural": sum(1 for c in clips for op in c["applied_ops"] if op.startswith(STRUCTURAL)),
            "director_s": plan["director_s"], "pacing_required": "REQUIRED" in plan["pacing"],
        })
    return out


def _judge(client, beat: dict) -> dict:
    card = beat["card"]
    card_text = "(none)" if card is None else (
        f"goal: {card['goal']}\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(card["candidates"], start=1)))
    direction = beat["direction"]
    user = (
        f"Viewer's latest direction: {direction['text'] + ' (given at segment ' + str(direction['segment']) + ')' if direction else '(none yet)'}\n"
        f"This beat {'answers that direction directly' if beat['answers'] else 'comes later, with no new line'}.\n\n"
        f"Scene card in force:\n{card_text}\n\n"
        f"The beat: {beat['intent']}\n" + "\n".join(f"- {a}" for a in beat["actions"])
    )
    resp = client.chat.completions.create(
        model=JUDGE_MODEL, temperature=0, seed=7,
        messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": "label", "strict": True, "schema": SCHEMA}},
    )
    return json.loads(resp.choices[0].message.content)


def _metrics(arm_name: str, arm: dict, beats: list[dict]) -> dict:
    lines = [p for p in arm["plans"] if p["answers"]]
    answered_segments = [b["segment"] for b in beats if b["answers"]]
    snap = []
    for i, seg in enumerate(answered_segments):
        nxt = answered_segments[i + 1] if i + 1 < len(answered_segments) else 10 ** 9
        window = [b for b in beats if seg <= b["segment"] < nxt]
        start = window[0]["story_second"] if window else 0
        back = next((b for b in window[1:] if b["label"] == "card"), None)
        held_until = next((b for b in window[1:] if b["label"] not in ("direction", "both")), None)
        end = window[-1]["story_second"] + window[-1]["seconds"] if window else 0
        snap.append({
            "line_segment": seg, "beats_in_window": len(window),
            "beats_to_card": (window.index(back) if back else None),
            "seconds_to_card": round(back["story_second"] - start, 1) if back else None,
            "direction_held_s": round((held_until["story_second"] if held_until else end) - start, 1),
            "labels": [b["label"] for b in window],
        })
    events = arm["events"]
    total_s = sum(e["duration"] for e in events)
    first_line = answered_segments[0] if answered_segments else 10 ** 9
    silent = [e for e in events if e["segment"] < first_line]
    # After the last line: the viewer is silent from its landing to the end.
    last_line = answered_segments[-1] if answered_segments else 1
    after = [e for e in events if e["segment"] > last_line]
    change_times, clock = [], 0.0
    for e in after:
        if any(op.startswith(STRUCTURAL) for op in e["applied_ops"]):
            change_times.append(clock)
        clock += e["duration"]
    gaps = [b - a for a, b in zip([0.0] + change_times, change_times + [clock])]
    answer_calls = [p["director_s"] for p in arm["plans"] if p["answers"]]
    other_calls = [p["director_s"] for p in arm["plans"] if not p["answers"]]
    stagnation_replans = arm["transcript"].count("Showrunner spawned: stagnation")
    return {
        "arm": arm_name, "segments": len(events), "story_seconds": round(total_s, 1),
        "lines_sent": len(arm["run"]["lines"]), "lines_answered": len(lines),
        "labels": {k: sum(1 for b in beats if b["label"] == k) for k in LABELS},
        "snap_back": snap,
        "returned_to_card": sum(1 for s in snap if s["beats_to_card"] is not None),
        "mean_direction_held_s": round(statistics.mean(s["direction_held_s"] for s in snap), 1) if snap else None,
        "structural_per_min": round(60 * sum(b["structural"] for b in beats) / total_s, 2) if total_s else None,
        "structural_per_min_before_first_line": round(
            60 * sum(1 for e in silent for op in e["applied_ops"] if op.startswith(STRUCTURAL))
            / max(1e-9, sum(e["duration"] for e in silent)), 2) if silent else None,
        "after_last_line_s": round(clock, 1),
        "structural_per_min_after_last_line": round(60 * len(change_times) / clock, 2) if clock else None,
        "longest_unchanged_after_last_line_s": round(max(gaps), 1) if gaps else None,
        "required_notes": sum(1 for b in beats if b["pacing_required"]),
        "stagnation_replans": stagnation_replans,
        "director_s": {
            "answer_mean": round(statistics.mean(answer_calls), 2) if answer_calls else None,
            "answer_max": round(max(answer_calls), 2) if answer_calls else None,
            "other_mean": round(statistics.mean(other_calls), 2) if other_calls else None,
            "other_max": round(max(other_calls), 2) if other_calls else None,
        },
        "answer_beat_clips": [b["planned_clips"] for b in beats if b["answers"]],
        "beat_clips": {n: sum(1 for b in beats if b["planned_clips"] == n) for n in range(1, 5)},
        "beat_seconds_mean": round(statistics.mean(b["seconds"] for b in beats), 1) if beats else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", type=Path)
    ap.add_argument("--rejudge", action="store_true", help="ask the judge again even where judgments exist")
    args = ap.parse_args(argv)
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    judgments_path = args.runs / "judgments.json"
    judged = json.loads(judgments_path.read_text()) if judgments_path.exists() and not args.rejudge else {}
    summary = {}
    for folder in sorted(p for p in args.runs.iterdir() if (p / "plans.jsonl").exists()):
        arm = _load_arm(folder)
        beats = _beats(arm)
        labels = judged.get(folder.name, {})
        for b in beats:
            key = str(b["beat"])
            if key not in labels:
                labels[key] = _judge(client, b)
            b.update(label=labels[key]["label"], candidate=labels[key]["candidate"], why=labels[key]["why"])
        judged[folder.name] = labels
        summary[folder.name] = _metrics(folder.name, arm, beats)
        summary[folder.name]["beats"] = [{k: b[k] for k in ("beat", "segment", "intent", "answers", "label", "candidate", "why")}
                                         for b in beats]
    groups: dict[str, list[dict]] = {}
    for folder_name, m in summary.items():
        run = json.loads((args.runs / folder_name / "run.json").read_text())
        groups.setdefault(f"{run['arm']}/{run.get('schedule', 'directions')}", []).append(m)
    aggregate = {}
    for key, runs in sorted(groups.items()):
        snaps = [x for m in runs for x in m["snap_back"]]
        after = [m["structural_per_min_after_last_line"] for m in runs if m["structural_per_min_after_last_line"] is not None]
        answers = [m["director_s"]["answer_mean"] for m in runs if m["director_s"]["answer_mean"] is not None]
        aggregate[key] = {
            "runs": len(runs), "lines": len(snaps),
            "returned_to_card": sum(1 for x in snaps if x["beats_to_card"] is not None),
            "beats_to_card": [x["beats_to_card"] for x in snaps],
            "direction_held_s_mean": round(statistics.mean(x["direction_held_s"] for x in snaps), 1) if snaps else None,
            "labels": {k: sum(m["labels"][k] for m in runs) for k in LABELS},
            "structural_per_min": round(statistics.mean(m["structural_per_min"] for m in runs), 2),
            "structural_per_min_after_last_line": round(statistics.mean(after), 2) if after else None,
            "longest_unchanged_after_last_line_s": max(m["longest_unchanged_after_last_line_s"] or 0 for m in runs),
            "required_notes": sum(m["required_notes"] for m in runs),
            "stagnation_replans": sum(m["stagnation_replans"] for m in runs),
            "answer_director_s_mean": round(statistics.mean(answers), 2) if answers else None,
            "other_director_s_mean": round(statistics.mean(m["director_s"]["other_mean"] for m in runs), 2),
            "answer_beat_clips": [c for m in runs for c in m["answer_beat_clips"]],
            "beat_clips": {n: sum(m["beat_clips"][n] for m in runs) for n in range(1, 5)},
        }
    summary["_aggregate"] = aggregate
    judgments_path.write_text(json.dumps(judged, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.runs / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{'arm':16} {'beats':>5} {'dir':>4} {'card':>4} {'both':>4} {'oth':>4} {'to card':>8} {'held s':>7} "
          f"{'struct/min':>10} {'REQ':>4} {'stag':>4} {'ans s':>6} {'oth s':>6}")
    for name, m in summary.items():
        if name.startswith("_"):
            continue
        lab = m["labels"]
        print(f"{name:16} {sum(lab.values()):5} {lab['direction']:4} {lab['card']:4} {lab['both']:4} {lab['other']:4} "
              f"{m['returned_to_card']:>3}/{len(m['snap_back']):<4} {m['mean_direction_held_s']!s:>7} "
              f"{m['structural_per_min']!s:>10} {m['required_notes']:4} {m['stagnation_replans']:4} "
              f"{m['director_s']['answer_mean']!s:>6} {m['director_s']['other_mean']!s:>6}")
    print()
    print(f"{'arm/schedule':28} {'runs':>4} {'to card':>8} {'held s':>7} {'str/min':>7} {'after':>6} {'gap s':>6} {'REQ':>4} {'stag':>4}")
    for key, a in aggregate.items():
        print(f"{key:28} {a['runs']:4} {a['returned_to_card']:>3}/{a['lines']:<4} {a['direction_held_s_mean']!s:>7} "
              f"{a['structural_per_min']:7} {a['structural_per_min_after_last_line']!s:>6} "
              f"{a['longest_unchanged_after_last_line_s']:6} {a['required_notes']:4} {a['stagnation_replans']:4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
