"""Style / continuity sweep against a running LTX-2.5 server. No LLM calls.

For each variant: reset the server session, render clip 1 from the
reference (a cut) and clip 2 continuing from it, with a fixed seed so
variants differ only in style text, negative prompt, continuity strength
and window. Clips land under logs/sweep/<variant>/; a contact sheet with
four frames per clip is written to logs/sweep/contact.png.

    python scripts/style_sweep.py [--server http://localhost:8000] [--only s1n1,base]
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

VARIANTS = {
    # name: (style, negative, continuity_strength, continuity_frames)
    "base":      (S0, N0, 1.0, 9),
    "s1":        (S1, N0, 1.0, 9),
    "s1n1":      (S1, N1, 1.0, 9),
    "s1n1_085":  (S1, N1, 0.85, 9),
    "s1n1_070":  (S1, N1, 0.7, 9),
    "s1n1_f17":  (S1, N1, 1.0, 17),
    "s2n1":      (S2, N1, 1.0, 9),
}


async def run_variant(name: str, server: str, seed: int, duration: int) -> list[str]:
    style, negative, strength, frames = VARIANTS[name]
    out = Path("logs/sweep") / name
    out.mkdir(parents=True, exist_ok=True)
    requests.post(f"{server}/reset", timeout=30)
    i2v = LtxvI2V(server_url=server, continuity_strength=strength, continuity_frames=frames,
                  output_dir=out, negative_prompt=negative, seed=seed)
    clips = []
    for turn, body, reset in ((1, BODY1, True), (2, BODY2, False)):
        path = await i2v.synthesize(image_path="data/legoman.png", prompt=f"{style} {body}",
                                    turn=turn, duration=duration, reset=reset)
        print(f"[{name}] clip {turn}: {path}", flush=True)
        if path:
            clips.append(path)
    (out / "variant.json").write_text(json.dumps(
        {"style": style, "negative": negative or "(server default)", "continuity_strength": strength,
         "continuity_frames": frames, "seed": seed, "clips": clips}, indent=2))
    return clips


def contact_sheet(names: list[str], out: Path) -> None:
    rows = []
    for name in names:
        clips = sorted((Path("logs/sweep") / name).glob("turn_*.mp4"))
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
        row = Path("logs/sweep") / f"{name}.row.png"
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
        labelled = Path("logs/sweep") / f"{name}.labelled.png"
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", str(row), "-vf",
                        f"drawtext=text='{name}':x=6:y=6:fontsize=20:fontcolor=red:box=1:boxcolor=white@0.7",
                        str(labelled)], check=False)
        rows.append(labelled if labelled.exists() else row)
    cmd = ["ffmpeg", "-v", "quiet", "-y"]
    for r in rows:
        cmd += ["-i", str(r)]
    cmd += ["-filter_complex", "".join(f"[{i}]" for i in range(len(rows))) + f"vstack={len(rows)}", str(out)]
    subprocess.run(cmd, check=True)
    print("contact sheet:", out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--duration", type=int, default=5)
    args = ap.parse_args()
    names = [n for n in args.only.split(",") if n] or list(VARIANTS)
    for name in names:
        await run_variant(name, args.server, args.seed, args.duration)
    contact_sheet(names, Path("logs/sweep/contact.png"))


if __name__ == "__main__":
    asyncio.run(main())
