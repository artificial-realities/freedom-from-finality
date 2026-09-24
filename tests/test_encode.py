"""The server's clip encode (RUN-14, `server/encode.py`): the pipeline's
frames become the same uint8 bytes on the GPU as on the host; the YUV 4:2:0
planes match ffmpeg's own conversion; a YUV-piped MP4 is as close to the
source as the RGB-piped one; the thread setting reaches x264. The torch halves run only
where torch is installed (the GPU box, or a CPU build:
`logs/_scratch/torchcpu/bin/python -m tests.run test_encode`)."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location("server_encode", Path(__file__).resolve().parents[1] / "server" / "encode.py")
encode = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(encode)

N, H, W = 17, 64, 96


def _video() -> np.ndarray:
    """A float clip in [0, 1] (F, H, W, 3): a moving gradient, a coloured
    square, a little noise, and values outside [0, 1] to clamp."""
    rng = np.random.default_rng(3)
    t = np.arange(N, dtype=np.float32)[:, None, None]
    x = np.linspace(0, 1, W, dtype=np.float32)[None, None, :]
    y = np.linspace(0, 1, H, dtype=np.float32)[None, :, None]
    v = np.stack([(x + 0.02 * t) % 1.0 * np.ones_like(y), y * np.ones_like(x) * np.ones_like(t), 0.5 * np.ones((N, H, W))], -1)
    for i in range(N):
        v[i, 20:36, 10 + 3 * i: 26 + 3 * i] = (0.9, 0.1, 0.1)
    v = v + rng.normal(0, 0.01, v.shape)
    v[0, 0, 0] = (-0.2, 1.3, 0.5)
    return v.astype(np.float32)


def _old_path(video: np.ndarray) -> np.ndarray:
    frames = video[0] if video.ndim == 5 else video
    return (np.clip(frames, 0.0, 1.0) * 255.0).round().astype(np.uint8)


def _torch():
    try:
        import torch
        return torch
    except ImportError:
        return None


def _decode(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def _probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,nb_frames,duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    return {line.split(",")[0]: line.split(",")[1:] for line in out.strip().splitlines()}


def test_host_frames_are_the_old_bytes():
    v = _video()
    assert np.array_equal(encode.frames_to_uint8(v[None]), _old_path(v[None])), "batched (B, F, H, W, C)"
    assert np.array_equal(encode.frames_to_uint8(v.transpose(0, 3, 1, 2)), _old_path(v)), "channels first"


def test_gpu_frames_are_the_host_bytes():
    torch = _torch()
    if torch is None:
        print("  (skipped: torch not installed)")
        return
    v = _video()
    for tensor in (torch.from_numpy(v[None]).permute(0, 1, 4, 2, 3),          # "pt": (B, F, C, H, W)
                   torch.from_numpy(v),                                         # (F, H, W, C)
                   torch.from_numpy(v[None]).permute(0, 1, 4, 2, 3).bfloat16()):
        host, on_device = encode.frames_to_uint8(tensor, keep_on_device=True)
        ref = _old_path(tensor.float().permute(0, 1, 3, 4, 2).numpy() if tensor.ndim == 5 else tensor.float().numpy())
        assert host.dtype == np.uint8 and np.array_equal(host, ref), tensor.shape
        assert torch.is_tensor(on_device) and on_device.dtype == torch.uint8
        assert encode.yuv420p_planes(on_device) == encode.yuv420p_planes(host), "device and host planes"


def test_yuv_planes_match_ffmpegs_conversion():
    """Our BT.601 limited-range planes against ffmpeg's rgb24 -> yuv420p:
    luma within one level, chroma (a 2x2 mean here, ffmpeg's own filter
    there) within a few levels, on average well under one."""
    u8 = _old_path(_video())
    ours = np.frombuffer(encode.yuv420p_planes(u8), np.uint8).reshape(N, -1).astype(np.int16)
    theirs = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-i", "-", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                            input=u8.tobytes(), capture_output=True, check=True).stdout
    theirs = np.frombuffer(theirs, np.uint8).reshape(N, -1).astype(np.int16)
    luma, chroma = slice(0, H * W), slice(H * W, None)
    assert np.abs(ours[:, luma] - theirs[:, luma]).max() <= 1
    diff = np.abs(ours[:, chroma] - theirs[:, chroma])
    assert diff.mean() < 1.0 and diff.max() <= 12, (diff.mean(), diff.max())


def test_a_yuv_piped_clip_decodes_like_an_rgb_piped_one():
    u8 = _old_path(_video())
    sr = 48000
    audio = (0.3 * np.sin(np.linspace(0, 2 * np.pi * 440 * N / 24, int(sr * N / 24)))).astype(np.float32)[None, :]
    with tempfile.TemporaryDirectory() as d:
        rgb, yuv = f"{d}/rgb.mp4", f"{d}/yuv.mp4"
        encode.encode_mp4(u8, 24, audio, sr, rgb)
        encode.encode_mp4(u8, 24, audio, sr, yuv, yuv=encode.yuv420p_planes(u8))
        a, b = _decode(rgb).astype(np.int16), _decode(yuv).astype(np.int16)
        assert a.shape == b.shape == (N, H, W, 3)
        # Two lossy encodes of nearly the same planes differ by codec noise;
        # what must hold is fidelity to the source: no worse than the RGB path.
        src = u8.astype(np.int16)
        err_rgb, err_yuv = np.abs(a - src).mean(), np.abs(b - src).mean()
        assert err_yuv <= err_rgb * 1.05 + 0.05, (err_rgb, err_yuv)
        pa, pb = _probe(rgb), _probe(yuv)
        assert pa["video"][0] == pb["video"][0] and pa["audio"] == pb["audio"], (pa, pb)


def test_the_thread_setting_reaches_x264():
    seen = []
    real = encode.subprocess.run

    def capture(cmd, **kw):
        seen.append(cmd)
        return real(cmd, **kw)

    encode.subprocess.run = capture
    try:
        with tempfile.TemporaryDirectory() as d:
            encode.encode_mp4(_old_path(_video()), 24, None, 48000, f"{d}/t.mp4", threads=8)
    finally:
        encode.subprocess.run = real
    cmd = seen[0]
    assert cmd[cmd.index("-threads") + 1] == "8" and cmd[cmd.index("-pix_fmt") + 1] == "rgb24"


def test_an_odd_frame_size_is_refused_for_yuv():
    try:
        encode.yuv420p_planes(np.zeros((2, 63, 96, 3), np.uint8))
        raise AssertionError("odd height accepted")
    except ValueError:
        pass
