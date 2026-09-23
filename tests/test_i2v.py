"""The seed image must reach the video model uncropped at the render's aspect ratio."""

from __future__ import annotations

import io

from PIL import Image

from src.i2v.ltxv import letterbox_reference


def test_letterbox_reference_auto_fill_matches_border():
    from src.i2v.ltxv import border_color
    src = Image.open("stories/lego-void/legoman.png")
    bc = border_color(src)
    png = letterbox_reference("stories/lego-void/legoman.png", 768, 512, fill="auto")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    px = img.load()
    assert px[5, 5] == bc, "padding continues the image's own background"
    assert all(abs(a - b) <= 2 for a, b in zip(px[5, 5], px[100, 256])), "no seam between pad and image edge"


def test_letterbox_reference_keeps_whole_subject():
    png = letterbox_reference("stories/lego-void/legoman.png", 768, 512, fill="white")
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
            p1 = i2v._synthesize_sync("stories/lego-void/legoman.png", "p1", 1, 5)
            p2 = i2v._synthesize_sync("stories/lego-void/legoman.png", "p2", 2, 7)
            assert p1 and p2 and Path(p1).exists()
            plain = ltxv.LtxvI2V(server_url="http://x", output_dir=d)
            plain._synthesize_sync("stories/lego-void/legoman.png", "p3", 1, 5)
    finally:
        ltxv.requests.get, ltxv.requests.post = orig_get, orig_post

    first, second, plain_first = posts
    assert "reference_image" in first and "reference_image" not in second, "reference sent once"
    assert "reset_window" not in first and "reset_window" not in second, "the client never asks for a re-seed"
    assert first["anchor_strength"] == 0.95 and first["anchor_index"] == -1
    assert second["anchor_strength"] == 0.95, "the anchor rides on continue shots too"
    assert second["num_frames"] == 169, "7 s at 24 fps on the 8k+1 grid"
    assert "anchor_strength" not in plain_first, "off by default: the field is not sent"
    assert "negative_prompt" not in plain_first and "negative_prompt" not in first, \
        "no negative prompt by default: the live config no longer sets one (LTX-08)"


class _FakeHTTP:
    """requests.get/post stand-ins for the LTX client: /health answers with
    a render size, /generate with a few bytes, every payload recorded."""

    def __init__(self, size=(960, 544), up=True):
        self.size, self.up, self.posts = size, up, []

    def __enter__(self):
        from src.i2v import ltxv

        class _Resp:
            def __init__(self, status=200, body=b"", js=None):
                self.status_code, self.content, self._js = status, body, js or {}
                self.headers, self.text = {}, ""

            def json(self):
                return self._js

        def get(url, timeout=0):
            if not self.up:
                raise ConnectionError("down")
            return _Resp(js={"status": "ready", "frame_rate": 24, "width": self.size[0], "height": self.size[1]})

        def post(url, json=None, timeout=0):
            self.posts.append(json)
            return _Resp(body=b"\x00" * 64)

        self._orig = ltxv.requests.get, ltxv.requests.post
        ltxv.requests.get, ltxv.requests.post = get, post
        return self

    def __exit__(self, *exc):
        from src.i2v import ltxv
        ltxv.requests.get, ltxv.requests.post = self._orig


def _png(path, size, colour="white", noisy_border=False):
    img = Image.new("RGB", size, colour)
    if noisy_border:
        px = img.load()
        for x in range(size[0]):
            for y in (0, 1, 2, 3):
                px[x, y] = (0, 0, 0) if (x // 3) % 2 else (255, 255, 255)
    img.save(path)
    return str(path)


def test_render_seed_is_derived_from_the_key_and_the_segment():
    """LTX-09: seed = hash(key, segment): stable across processes, distinct
    per segment and per key, never one fixed seed, and none sent (the
    server draws one) only when no key is set."""
    import tempfile
    from src.i2v.ltxv import LtxvI2V, derive_seed
    assert derive_seed("sess", 1) == derive_seed("sess", 1)
    seeds = {derive_seed("sess", n) for n in range(1, 200)}
    assert len(seeds) == 199 and all(0 <= s < 2 ** 32 for s in seeds)
    assert derive_seed("sess", 1) != derive_seed("other", 1)
    # A fixed expectation, so a change of hash (which would silently change every rerun) fails here.
    assert derive_seed("director-ltxv_live_20260918_145552", 1) == int.from_bytes(
        __import__("hashlib").sha256(b"director-ltxv_live_20260918_145552:1").digest()[:4], "big")
    with tempfile.TemporaryDirectory() as d, _FakeHTTP() as http:
        keyed = LtxvI2V(server_url="http://x", output_dir=d, seed_key="sess")
        keyed._synthesize_sync("stories/lego-void/legoman.png", "a", 1, 6)
        assert keyed.last_render["seed"] == derive_seed("sess", 1)
        keyed._synthesize_sync("stories/lego-void/legoman.png", "b", 2, 6)
        rerun = LtxvI2V(server_url="http://x", output_dir=d, seed_key="sess")
        rerun._synthesize_sync("stories/lego-void/legoman.png", "a", 1, 6)
        LtxvI2V(server_url="http://x", output_dir=d)._synthesize_sync("stories/lego-void/legoman.png", "c", 1, 6)
    first, second, again, unkeyed = http.posts
    assert first["seed"] == derive_seed("sess", 1) and second["seed"] == derive_seed("sess", 2)
    assert again["seed"] == first["seed"], "a rerun under the same key reproduces the noise"
    assert "seed" not in unkeyed


def test_the_runner_keys_seeds_to_the_session():
    """LTX-09 in the runner: the key is the session id unless the config
    names another run's."""
    import tempfile
    from src.eval.runner import _maybe_i2v
    from src.models.config import Config
    base = dict(name="t", graph="director", llm_backend="mock", model="mock", video_enabled=True,
                i2v_backend="ltxv", ltxv_server_url="http://x")
    with tempfile.TemporaryDirectory() as d:
        assert _maybe_i2v(Config(**base), d, "director_live_1").seed_key == "director_live_1"
        assert _maybe_i2v(Config(**base, ltxv_seed_key="old_run"), d, "director_live_1").seed_key == "old_run"
    assert "ltxv_seed" not in Config.model_fields, "no fixed seed"
    old = Config(**base, ltxv_seed=7)   # an old config with the removed key still loads (ignored)
    assert old.ltxv_seed_key == ""


def test_reference_is_fitted_to_the_render_frame():
    """RUN-11: within 5 % of the render aspect the reference is
    cover-resized (no padding); outside it `pad` letterboxes with a
    warning and `strict` refuses; a small image and an uneven border
    are warned about."""
    import tempfile
    from pathlib import Path
    from src.i2v.ltxv import ReferenceRefused, fit_reference
    with tempfile.TemporaryDirectory() as d:
        master = _png(Path(d) / "master.png", (1000, 551), "red")      # 1.815 vs 1.765: 2.8 % off
        png, notes = fit_reference(master, 960, 544)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        assert img.size == (960, 544) and notes == []
        assert img.getpixel((0, 0)) == (255, 0, 0) and img.getpixel((959, 543)) == (255, 0, 0), "covered, not padded"

        small = _png(Path(d) / "small.png", (480, 272), "red")
        png, notes = fit_reference(small, 960, 544)
        assert Image.open(io.BytesIO(png)).size == (960, 544) and any("smaller than the render size" in n for n in notes)

        square = "stories/lego-void/legoman.png"
        png, notes = fit_reference(square, 960, 544, fit="pad")
        assert Image.open(io.BytesIO(png)).size == (960, 544)
        assert "letterboxed" in notes[0], notes
        assert any("smaller than the subject area" in n for n in notes), "380 px of figure in a 435 px subject area"
        try:
            fit_reference(square, 960, 544, fit="strict")
            raise AssertionError("strict must refuse a square reference for a 16:9 frame")
        except ReferenceRefused as exc:
            assert "strict" in str(exc)
        png, notes = fit_reference(master, 960, 544, fit="strict")
        assert notes == [], "strict accepts a master at the render aspect"

        framed = _png(Path(d) / "framed.png", (500, 500), "white", noisy_border=True)
        _, notes = fit_reference(framed, 960, 544)
        assert any("border is not uniform" in n for n in notes), notes


def test_reference_is_checked_before_the_run_renders():
    """RUN-11 at run start: the client checks against the probed size and
    the verdict goes to the timing log; strict stops the run there; an
    unreachable server leaves the check to the first render."""
    import json
    import tempfile
    from pathlib import Path
    from src.eval.runner import _check_reference
    from src.i2v.ltxv import LtxvI2V, ReferenceRefused
    from src.util.timing_log import TimingLog
    with tempfile.TemporaryDirectory() as d:
        timing = TimingLog(Path(d) / "timing.jsonl")
        with _FakeHTTP(size=(640, 352)):
            _check_reference(LtxvI2V(server_url="http://x", output_dir=d), "stories/lego-void/legoman.png", timing)
            master = _png(Path(d) / "m.png", (1280, 704))
            _check_reference(LtxvI2V(server_url="http://x", output_dir=d, reference_fit="strict"), master, timing)
            try:
                _check_reference(LtxvI2V(server_url="http://x", output_dir=d, reference_fit="strict"),
                                 "stories/lego-void/legoman.png", timing)
                raise AssertionError("strict must stop the run")
            except ReferenceRefused:
                pass
        with _FakeHTTP(up=False):
            _check_reference(LtxvI2V(server_url="http://x", output_dir=d), "stories/lego-void/legoman.png", timing)
        _check_reference(None, "stories/lego-void/legoman.png", timing)
        timing.close()
        records = [json.loads(line) for line in (Path(d) / "timing.jsonl").read_text().splitlines()]
    pad, cover, refused, later = records
    assert pad["event"] == "reference" and pad["fit"] == "pad" and pad["size"] == [640, 352] and pad["warnings"]
    assert cover["fit"] == "cover" and cover["warnings"] == []
    assert "strict" in refused["refused"]
    assert later["checked"] is False
    try:
        LtxvI2V(server_url="http://x", output_dir="logs/_scratch", reference_fit="crop")
        raise AssertionError("an unknown fit is refused")
    except ValueError:
        pass


def test_story_load_checks_the_reference():
    """RUN-11 at load: the seed image exists and decodes, and the
    protagonist's reference_pose says what it shows."""
    import json
    import shutil
    import tempfile
    from pathlib import Path
    from src.models.story import Story
    story = Story.from_json("stories/lego-void")
    assert story.characters[0].reference_pose
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "s"
        shutil.copytree("stories/lego-void", folder)
        blueprint = json.loads((folder / "story.json").read_text())

        def load_with(**change):
            data = json.loads(json.dumps(blueprint))
            for key, value in change.items():
                if key == "pose":
                    data["characters"][0]["reference_pose"] = value
                else:
                    data[key] = value
            (folder / "story.json").write_text(json.dumps(data))
            return Story.from_json(folder)

        for change, words in (({"seed_image": "missing.png"}, "does not exist"),
                              ({"pose": "  "}, "reference_pose")):
            try:
                load_with(**change)
                raise AssertionError(f"{change} must be refused")
            except ValueError as exc:
                assert words in str(exc), exc
        (folder / "broken.png").write_bytes(b"not an image")
        try:
            load_with(seed_image="broken.png")
            raise AssertionError("an undecodable image must be refused")
        except ValueError as exc:
            assert "does not decode" in str(exc)
        assert load_with(seed_image="", pose="").seed_image == "", "a story without an image needs no pose"
