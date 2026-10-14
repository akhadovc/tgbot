"""Turn a short video into what gpt-6.1-sol can read: a few frames + a transcript.

gpt-6.1-sol takes text and images only, so the video is sampled into evenly
spaced JPEG frames with ffmpeg and its audio is transcribed separately.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("diddy.video")

FRAME_WIDTH = 768


class VideoError(Exception):
    """Raised with a user-facing Uzbek message."""


@dataclass
class VideoSample:
    duration: float
    frames: list[tuple[float, bytes]] = field(default_factory=list)  # (second, jpeg)
    audio_path: Path | None = None
    audio_seconds: float = 0.0


def ffmpeg_exe() -> str:
    """Bundled ffmpeg from imageio-ffmpeg (pip), else one on PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise VideoError("Videoni ochish uchun ffmpeg topilmadi. Egasi `pip install imageio-ffmpeg` qilsin.")


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, creationflags=flags)


_DURATION = re.compile(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO = re.compile(rb"Stream #\S+.*?: Audio:")


def probe(ffmpeg: str, path: Path) -> tuple[float, bool]:
    """(duration seconds, has audio) read from ffmpeg's own header dump."""
    result = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)], timeout=30)
    match = _DURATION.search(result.stderr)
    if not match:
        raise VideoError("Bu videoni ocholmadim — fayl buzilgan yoki format g'alati 🗿")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return duration, bool(_AUDIO.search(result.stderr))


def frame_times(duration: float, count: int) -> list[float]:
    """Evenly spaced points, avoiding the very first/last frame (often black)."""
    if duration <= 0:
        return [0.0]
    count = max(1, count)
    step = duration / (count + 1)
    return [round(step * (i + 1), 2) for i in range(count)]


def extract_frame(ffmpeg: str, path: Path, second: float, out: Path) -> bytes | None:
    result = _run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{second:.2f}",
                   "-i", str(path), "-frames:v", "1", "-vf", f"scale='min({FRAME_WIDTH},iw)':-2",
                   "-q:v", "5", "-y", str(out)], timeout=30)
    if result.returncode != 0 or not out.exists():
        return None
    data = out.read_bytes()
    return data or None


def extract_audio(ffmpeg: str, path: Path, out: Path, max_seconds: int) -> bool:
    result = _run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
                   "-t", str(max_seconds), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                   "-y", str(out)], timeout=120)
    return result.returncode == 0 and out.exists() and out.stat().st_size > 1024


def sample_video(path: Path, workdir: Path, *, frames: int, max_audio_seconds: int,
                 with_audio: bool = True) -> VideoSample:
    ffmpeg = ffmpeg_exe()
    duration, has_audio = probe(ffmpeg, path)
    sample = VideoSample(duration=duration)
    for index, second in enumerate(frame_times(duration, frames)):
        data = extract_frame(ffmpeg, path, second, workdir / f"frame_{index:02d}.jpg")
        if data:
            sample.frames.append((second, data))
    if not sample.frames:
        raise VideoError("Videodan birorta ham kadr chiqmadi. Boshqa formatda tashla.")
    if with_audio and has_audio:
        audio = workdir / "audio.mp3"
        if extract_audio(ffmpeg, path, audio, max_audio_seconds):
            sample.audio_path = audio
            sample.audio_seconds = min(duration, max_audio_seconds)
    return sample


async def sample_video_async(path: Path, workdir: Path, **kwargs) -> VideoSample:
    try:
        return await asyncio.to_thread(sample_video, path, workdir, **kwargs)
    except subprocess.TimeoutExpired:
        raise VideoError("Videoni qayta ishlash juda uzoq ketdi, qisqaroq video tashla.") from None


def temp_workdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="diddy-video-")
