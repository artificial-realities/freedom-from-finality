"""The seed image must reach the video model uncropped at the render's aspect ratio."""

from __future__ import annotations

import io

from PIL import Image

from src.i2v.ltxv import letterbox_reference


def test_letterbox_reference_auto_fill_matches_border():
    from src.i2v.ltxv import border_color
    src = Image.open("data/legoman.png")
    bc = border_color(src)
    png = letterbox_reference("data/legoman.png", 768, 512, fill="auto")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    px = img.load()
    assert px[5, 5] == bc, "padding continues the image's own background"
    assert all(abs(a - b) <= 2 for a, b in zip(px[5, 5], px[100, 256])), "no seam between pad and image edge"


def test_letterbox_reference_keeps_whole_subject():
    png = letterbox_reference("data/legoman.png", 768, 512, fill="white")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    assert img.size == (768, 512)
    px = img.load()
    # Margins are fill; the subject sits in the middle.
    assert px[5, 5] == (255, 255, 255) and px[762, 506] == (255, 255, 255)
    assert px[10, 256] == (255, 255, 255), "left margin is canvas, nothing cropped"
    centre_nonwhite = sum(1 for y in range(0, 512, 4) if px[384, y] != (255, 255, 255))
    assert centre_nonwhite > 60, "the figure occupies most of the centre column"
    # Top row of the subject (the head) is inside the frame: nothing non-white touches the top edge.
    assert all(px[x, 0] == (255, 255, 255) for x in range(0, 768, 8))


def test_letterbox_reference_handles_wide_images():
    src = Image.new("RGB", (1000, 100), "red")
    buf = io.BytesIO(); src.save(buf, format="PNG"); buf.seek(0)
    png = letterbox_reference(buf, 768, 512, fill="black")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    assert img.size == (768, 512)
    px = img.load()
    assert px[384, 256] == (255, 0, 0) and px[384, 5] == (0, 0, 0)
