"""Style / continuity / substrate sweep against a running LTX-2.5 server. No LLM calls.

For each variant: reset the server session, render clip 1 from the
reference (a cut) and clip 2 continuing from it, with a fixed seed so
variants differ only in style text, negative prompt, continuity strength,
window and identity anchor. Clips land under <out>/<variant>/ (--out, default logs/sweep); a
contact sheet with four frames per clip is written to <out>/contact.png.

    python scripts/style_sweep.py [--server http://localhost:8000] [--only s1n1,base] [--out logs/sweep]
    python scripts/style_sweep.py --only kick,kick_a095,kick_a070,kick_amid   # 2026-09-09 substrate set

The substrate set (default) uses the action that drifted in every live
clip (a kick) and compares no anchor against the reference image as a
guiding keyframe at the end or the middle of the clip. Run it once per
server variant (LTX2_VARIANT=distilled, then full) to compare recipes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, so `src` imports

import requests  # noqa: E402

from src.i2v.ltxv import LtxvI2V  # noqa: E402
from src.models.story import Story  # noqa: E402

STORY = Story.from_json("data/story.json")
LOOK = STORY.characters[0].look

S0 = " ".join(STORY.visual_style.split())
S1 = ("Clean product photograph of a single LEGO minifigure on a seamless pure white cyclorama. "
      "Even, shadowless studio light; only a faint soft contact shadow under the figure. Flat neutral "
      "colours, no coloured light, no glow, no texture, no floor line, no horizon. Locked-off 50mm lens on a "
      "tripod. The background stays pure white for the entire shot.")
S2 = S1 + " Realistic plastic materials with a slight stop-motion feel to the movement."

N0 = ""  # server default: "worst quality, inconsistent motion, blurry, jittery, distorted, static"
N1 = ("worst quality, inconsistent motion, blurry, jittery, distorted, green skin, green face, glowing, neon, "
      "coloured light, spotlight, lens flare, grey background, dark background, textured floor, hair, "
      "eyebrows, extra characters, text, watermark")

BODY1 = (f"Wide shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). Lego Man lies on "
         "his back in the white void. He twitches one hand, sits up stiffly with a click, and turns his head "
         "left, then right, taking in the emptiness. Final frame: Lego Man sitting upright, head turned to the right.")
BODY2 = (f"Medium shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). Lego Man pushes "
         "himself up to standing, brushes off his torso with both claw hands, takes two careful steps forward "
         "and stops, arms at his sides. Final frame: Lego Man standing still, facing the camera.")

BODY1_STAND = (f"Wide shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). Lego Man stands "
               "facing the camera with his arms at his sides. He turns his head left, then right, taking in the "
               "emptiness, then lifts one claw hand and looks at it. Final frame: Lego Man standing, looking at his hand.")

# The action that drifted in every clip of the 2026-09-09 live run: as soon
# as he moves, the face and torso gain prints and the ground greys.
POSE = STORY.characters[0].reference_pose
BODY_KICK = (f"Medium shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). The shot opens "
             f"with Lego Man {POSE}, then the action begins. A classic black-and-white LEGO soccer ball rolls in "
             "from the left and stops at his feet. He looks down at it, draws his right leg back with a click and "
             "kicks the ball; it rolls out of frame to the right. He watches it go, then returns to standing with "
             "his arms at his sides. Final frame: Lego Man standing upright, facing the camera, arms at his sides.")
BODY_AFTER = (f"Medium shot, straight-on, eye level, static camera. On screen: Lego Man ({LOOK}). Lego Man takes "
              "two careful steps to the right, stops, and raises both claw hands in a small shrug. Final frame: "
              "Lego Man standing, hands raised in a shrug, facing the camera.")

VARIANTS = {
    # name: dict(style, negative, strength, frames[, body1, seed, clips])
    "base":      dict(style=S0, negative=N0, strength=1.0, frames=9),
    "s1":        dict(style=S1, negative=N0, strength=1.0, frames=9),
    "s1n1":      dict(style=S1, negative=N1, strength=1.0, frames=9),
    "s1n1_085":  dict(style=S1, negative=N1, strength=0.85, frames=9),
    "s1n1_070":  dict(style=S1, negative=N1, strength=0.7, frames=9),
    "s1n1_f17":  dict(style=S1, negative=N1, strength=1.0, frames=17),
    "s2n1":      dict(style=S2, negative=N1, strength=1.0, frames=9),
    # Opening pose and seed: does starting from the reference pose, or a
    # different seed, remove the halo and the invented hair?
    "stand_s1234": dict(style=S1, negative=N1, strength=1.0, frames=9, body1=BODY1_STAND, seed=1234, clips=1),
    "stand_s7":    dict(style=S1, negative=N1, strength=1.0, frames=9, body1=BODY1_STAND, seed=7, clips=1),
    "stand_s99":   dict(style=S1, negative=N1, strength=1.0, frames=9, body1=BODY1_STAND, seed=99, clips=1),
    "lie_s7":      dict(style=S1, negative=N1, strength=1.0, frames=9, seed=7, clips=1),
    # 2026-09-09 substrate set: the live blueprint style and negative prompt,
    # the kick, and the reference image as a guiding keyframe (anchor).
    # anchor_index is in latent frames: -1 = the last, 8 = the middle of a
    # 121-frame clip (16 latent frames).
    "kick":      dict(style=S0, negative=N1, strength=1.0, frames=9, body1=BODY_KICK, body2=BODY_AFTER, anchor=0.0),
    "kick_a095": dict(style=S0, negative=N1, strength=1.0, frames=9, body1=BODY_KICK, body2=BODY_AFTER, anchor=0.95),
    "kick_a070": dict(style=S0, negative=N1, strength=1.0, frames=9, body1=BODY_KICK, body2=BODY_AFTER, anchor=0.70),
    "kick_amid": dict(style=S0, negative=N1, strength=1.0, frames=9, body1=BODY_KICK, body2=BODY_AFTER, anchor=0.95, anchor_index=8),
}
SUBSTRATE_SET = ["kick", "kick_a095", "kick_a070", "kick_amid"]
ROOT = Path("logs/sweep")  # set from --out; one root per server variant


async def run_variant(name: str, server: str, seed: int, duration: int) -> list[str]:
    v = VARIANTS[name]
    style, negative, strength, frames = v["style"], v["negative"], v["strength"], v["frames"]
    seed = v.get("seed", seed)
    body1 = v.get("body1", BODY1)
    body2 = v.get("body2", BODY2)
    anchor, anchor_index = v.get("anchor", 0.0), v.get("anchor_index", -1)
    plan = [(1, body1, True), (2, body2, False)][: v.get("clips", 2)]
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    requests.post(f"{server}/reset", timeout=30)
    i2v = LtxvI2V(server_url=server, continuity_strength=strength, continuity_frames=frames,
                  output_dir=out, negative_prompt=negative, seed=seed,
                  anchor_strength=anchor, anchor_index=anchor_index)
    clips, latencies = [], []
    for turn, body, reset in plan:
        path = await i2v.synthesize(image_path="data/legoman.png", prompt=f"{style} {body}",
                                    turn=turn, duration=duration, reset=reset)
        print(f"[{name}] clip {turn}: {path} ({i2v.last_latency_s}s render)", flush=True)
        if path:
            clips.append(path)
            latencies.append(i2v.last_latency_s)
    health = {}
    try:
        health = requests.get(f"{server}/health", timeout=10).json()
    except Exception:
        pass
    (out / "variant.json").write_text(json.dumps(
        {"style": style, "negative": negative or "(server default)", "continuity_strength": strength,
         "continuity_frames": frames, "seed": seed, "body1": body1, "body2": body2,
         "anchor_strength": anchor, "anchor_index": anchor_index,
         "server": {k: health.get(k) for k in ("variant", "steps", "recipe", "width", "height")},
         "clips": clips, "latency_s": latencies}, indent=2))
    return clips


def contact_sheet(names: list[str], out: Path) -> None:
    rows = []
    for name in names:
        clips = sorted((ROOT / name).glob("turn_*.mp4"))
        if not clips:
            continue
        strips = []
        for clip in clips:
            strip = clip.with_suffix(".strip.png")
            frames = int(subprocess.run(["ffprobe", "-v", "quiet", "-count_frames", "-show_entries",
                                         "stream=nb_read_frames", "-of", "csv=p=0", "-select_streams", "v",
                                         str(clip)], capture_output=True, text=True).stdout.strip() or 1)
            picks = "+".join(f"eq(n\\,{int(frames * f)})" for f in (0.0, 0.33, 0.66)) + f"+eq(n\\,{frames - 1})"
            subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", str(clip), "-vf",
                            f"select='{picks}',scale=192:-1,tile=4x1", "-frames:v", "1", str(strip)], check=True)
            strips.append(strip)
        row = ROOT / f"{name}.row.png"
        cmd = ["ffmpeg", "-v", "quiet", "-y"]
        for s in strips:
            cmd += ["-i", str(s)]
        cmd += ["-filter_complex", "".join(f"[{i}]" for i in range(len(strips))) + f"hstack={len(strips)}",
                str(row)] if len(strips) > 1 else [str(row)]
        if len(strips) > 1:
            subprocess.run(cmd, check=True)
        else:
            row = strips[0]
        # label the row with the variant name
        labelled = ROOT / f"{name}.labelled.png"
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", str(row), "-vf",
                        f"drawtext=text='{name}':x=6:y=6:fontsize=20:fontcolor=red:box=1:boxcolor=white@0.7",
                        str(labelled)], check=False)
        rows.append(labelled if labelled.exists() else row)
    if len(rows) == 1:
        shutil.copy(rows[0], out)
    else:
        cmd = ["ffmpeg", "-v", "quiet", "-y"]
        for r in rows:
            cmd += ["-i", str(r)]
        cmd += ["-filter_complex", "".join(f"[{i}]" for i in range(len(rows))) + f"vstack={len(rows)}", str(out)]
        subprocess.run(cmd, check=True)
    print("contact sheet:", out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--only", default="", help="comma-separated variant names (default: the substrate set)")
    ap.add_argument("--out", default="logs/sweep", help="output root, e.g. logs/sweep/distilled")
    ap.add_argument("--sheet", default="", help="contact sheet path (default: <out>/contact.png)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--duration", type=int, default=5)
    args = ap.parse_args()
    global ROOT
    ROOT = Path(args.out)
    ROOT.mkdir(parents=True, exist_ok=True)
    names = [n for n in args.only.split(",") if n] or SUBSTRATE_SET
    for name in names:
        await run_variant(name, args.server, args.seed, args.duration)
    contact_sheet(names, Path(args.sheet) if args.sheet else ROOT / "contact.png")


if __name__ == "__main__":
    asyncio.run(main())
