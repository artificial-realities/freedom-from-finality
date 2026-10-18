"""The CPU side of a clip on the LTX-2.5 server: the pipeline's frames to
uint8, and the MP4.

Done naively, 1.4 s of a 4.9 s render was spent outside the model: the
float-to-uint8 conversion of the pipeline's output on the CPU, ffmpeg's
RGB-to-YUV conversion and the x264 encode.

- `frames_to_uint8` takes the pipeline's output as it comes: a torch
  tensor on the GPU is converted there, with the same float32 arithmetic
  as the numpy path, so the bytes are identical.
- `yuv420p_planes` converts uint8 RGB to planar YUV 4:2:0 (BT.601,
  limited range, the matrix and range ffmpeg's default RGB-to-yuv420p
  conversion uses; chroma is the mean of each 2x2 block), on the GPU for
  a tensor. Piping it halves the bytes and removes ffmpeg's conversion.
- `encode_mp4` pipes the frames into ffmpeg's libx264, with the audio from
  a WAV padded to the video and held under a loudness ceiling
  (`audio_ceiling.py`).

Imported by `ltx2_server.py`; numpy only at import, torch only when a
tensor is passed, so the tests run without torch.
"""

from __future__ import annotations

import os
import subprocess
import wave

import numpy as np

# BT.601 limited range, the coefficients ffmpeg's swscale uses by default
# for rgb24 -> yuv420p (Y 16-235, Cb/Cr 16-240).
_Y = (0.256788, 0.504129, 0.097906)
_CB = (-0.148223, -0.290993, 0.439216)
_CR = (0.439216, -0.367788, -0.071427)


def _is_tensor(x) -> bool:
    return type(x).__module__.startswith("torch")


def _layout_fhwc(video):
    """(F, H, W, 3) from (B, F, ...) or channels-first (F, 3, H, W)."""
    if video.ndim == 5:
        video = video[0]
    if video.shape[-1] != 3 and video.shape[1] == 3:
        video = video.permute(0, 2, 3, 1) if _is_tensor(video) else video.transpose(0, 2, 3, 1)
    return video


def frames_to_uint8(video, *, keep_on_device: bool = False):
    """The pipeline's frames in [0, 1] as uint8 (F, H, W, 3).

    A torch tensor is converted where it lives (the GPU) in float32, then
    copied to the host; `keep_on_device=True` also returns the uint8
    tensor on the device, for `yuv420p_planes`. A numpy array takes the
    host path. Same arithmetic both ways: clamp, x255, round half to even."""
    video = _layout_fhwc(video)
    if _is_tensor(video):
        u8 = (video.float().clamp(0.0, 1.0) * 255.0).round().to(dtype=_torch().uint8).contiguous()
        host = u8.cpu().numpy()
        return (host, u8) if keep_on_device else host
    host = (np.clip(video.astype(np.float32, copy=False), 0.0, 1.0) * 255.0).round().astype(np.uint8)
    return (host, host) if keep_on_device else host


def _torch():
    import torch
    return torch


def yuv420p_planes(frames_u8) -> bytes:
    """Planar YUV 4:2:0 bytes (per frame: Y, then U, then V) from uint8
    RGB (F, H, W, 3) with even H and W; on the device for a tensor."""
    n, h, w, _ = frames_u8.shape
    if h % 2 or w % 2:
        raise ValueError(f"yuv420p needs an even frame size, not {w}x{h}")
    if _is_tensor(frames_u8):
        torch = _torch()
        f = frames_u8.float()
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        y = 16.0 + _Y[0] * r + _Y[1] * g + _Y[2] * b
        cb = 128.0 + _CB[0] * r + _CB[1] * g + _CB[2] * b
        cr = 128.0 + _CR[0] * r + _CR[1] * g + _CR[2] * b
        cb = cb.reshape(n, h // 2, 2, w // 2, 2).mean(dim=(2, 4))
        cr = cr.reshape(n, h // 2, 2, w // 2, 2).mean(dim=(2, 4))
        planes = [p.round().clamp(0, 255).to(torch.uint8).reshape(n, -1) for p in (y, cb, cr)]
        return torch.cat(planes, dim=1).cpu().numpy().tobytes()
    f = frames_u8.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 16.0 + _Y[0] * r + _Y[1] * g + _Y[2] * b
    cb = 128.0 + _CB[0] * r + _CB[1] * g + _CB[2] * b
    cr = 128.0 + _CR[0] * r + _CR[1] * g + _CR[2] * b
    cb = cb.reshape(n, h // 2, 2, w // 2, 2).mean(axis=(2, 4))
    cr = cr.reshape(n, h // 2, 2, w // 2, 2).mean(axis=(2, 4))
    planes = [np.clip(np.round(p), 0, 255).astype(np.uint8).reshape(n, -1) for p in (y, cb, cr)]
    return np.concatenate(planes, axis=1).tobytes()


def encode_mp4(frames_u8: np.ndarray, fps: int, audio, sample_rate: int, path: str, *,
               yuv: bytes | None = None, threads: int = 0, ceiling_lufs: float | None = None) -> dict:
    """Raw frames piped into ffmpeg's libx264, the audio waveform
    [channels, samples] muxed from a temporary WAV.

    `yuv`: the frames already as planar YUV 4:2:0 (`yuv420p_planes`),
    piped instead of RGB so ffmpeg does no conversion. `threads`: x264's
    thread count (0 = ffmpeg's choice). With `ceiling_lufs`, the audio is
    padded to the video and given one static, never-boosting gain to the
    ceiling and under -1 dBTP; the measurements come back for the
    response headers (LUFS, TP, Gain-dB)."""
    n, h, w, _ = frames_u8.shape
    wav = None
    pix = "yuv420p" if yuv is not None else "rgb24"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix,
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if audio is not None:
        wav = path + ".wav"
        pcm = (audio.clamp(-1.0, 1.0).T.contiguous().numpy() * 32767.0).astype(np.int16) if _is_tensor(audio) \
            else (np.clip(np.asarray(audio, np.float32).T, -1.0, 1.0) * 32767.0).astype(np.int16)
        with wave.open(wav, "wb") as f:
            f.setnchannels(pcm.shape[1])
            f.setsampwidth(2)
            f.setframerate(int(sample_rate))
            f.writeframes(pcm.tobytes())
        cmd += ["-i", wav]
    meta: dict = {}
    if audio is not None and ceiling_lufs is not None:
        import audio_ceiling
        lufs, peak = audio_ceiling.measure(wav)
        gain = audio_ceiling.gain_db(lufs, peak, ceiling_lufs)
        cmd += ["-af", audio_ceiling.audio_filter(gain, n / fps)]
        meta = {"LUFS": f"{lufs:.2f}" if lufs is not None else "nan",
                "TP": f"{peak:.2f}" if peak is not None else "nan", "Gain-dB": f"{gain:.2f}"}
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "20",
            "-pix_fmt", "yuv420p", "-threads", str(int(threads))]
    if audio is not None:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-movflags", "+faststart", path]
    try:
        subprocess.run(cmd, input=yuv if yuv is not None else frames_u8.tobytes(), check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg encode failed: {exc.stderr.decode(errors='replace')[-500:]}") from exc
    finally:
        if wav and os.path.exists(wav):
            os.unlink(wav)
    return meta
