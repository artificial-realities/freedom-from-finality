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


def test_ltxv_payload_carries_anchor_and_reference(monkeypatch=None):
    """The client sends the identity anchor only when enabled, and the
    reference image only on the first call (or after a server restart)."""
    import json
    import tempfile
    from pathlib import Path

    from src.i2v import ltxv

    posts: list[dict] = []

    class _Resp:
        def __init__(self, status=200, body=b"", js=None):
            self.status_code, self.content, self._js = status, body, js or {}
            self.headers, self.text = {}, ""
        def json(self):
            return self._js

    def fake_get(url, timeout=0):
        return _Resp(js={"status": "ready", "frame_rate": 24, "width": 960, "height": 544})

    def fake_post(url, json=None, timeout=0):
        posts.append(json)
        return _Resp(body=b"\x00" * 64)

    orig_get, orig_post = ltxv.requests.get, ltxv.requests.post
    ltxv.requests.get, ltxv.requests.post = fake_get, fake_post
    try:
        with tempfile.TemporaryDirectory() as d:
            i2v = ltxv.LtxvI2V(server_url="http://x", output_dir=d, anchor_strength=0.95, anchor_index=-1)
            assert i2v.size == (960, 544), "render size probed from /health"
            assert i2v.seconds_to_frames(5) == 121
            p1 = i2v._synthesize_sync("data/legoman.png", "p1", 1, 5)
            p2 = i2v._synthesize_sync("data/legoman.png", "p2", 2, 7)
            assert p1 and p2 and Path(p1).exists()
            plain = ltxv.LtxvI2V(server_url="http://x", output_dir=d)
            plain._synthesize_sync("data/legoman.png", "p3", 1, 5)
    finally:
        ltxv.requests.get, ltxv.requests.post = orig_get, orig_post

    first, second, plain_first = posts
    assert "reference_image" in first and "reference_image" not in second, "reference sent once"
    assert "reset_window" not in first and "reset_window" not in second, "the client never asks for a re-seed"
    assert first["anchor_strength"] == 0.95 and first["anchor_index"] == -1
    assert second["anchor_strength"] == 0.95, "the anchor rides on continue shots too"
    assert second["num_frames"] == 169, "7 s at 24 fps on the 8k+1 grid"
    assert "anchor_strength" not in plain_first, "off by default: the field is not sent"
