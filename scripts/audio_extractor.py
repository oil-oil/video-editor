#!/usr/bin/env python3
"""Audio extraction and level analysis from video files using ffmpeg."""

from __future__ import annotations

import array
import math
import subprocess
import sys
import wave
from pathlib import Path
from common import log


def probe_video_info(video_path: Path) -> dict[str, float | str]:
    """Get video duration, width, height, framerate, and audio presence via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    import json
    data = json.loads(result.stdout)
    duration_s = float((data.get("format") or {}).get("duration", 0.0))
    has_audio = False
    width = 1920
    height = 1080
    fps = 30.0
    for s in data.get("streams") or []:
        if s.get("codec_type") == "audio":
            has_audio = True
        elif s.get("codec_type") == "video":
            if s.get("width"):
                width = int(s["width"])
            if s.get("height"):
                height = int(s["height"])
            r_fps = s.get("r_frame_rate", "30/1")
            try:
                num, den = r_fps.split("/")
                fps = float(num) / float(den) if float(den) else 30.0
            except Exception:
                pass
    return {
        "duration_s": duration_s,
        "has_audio": has_audio,
        "width": width,
        "height": height,
        "fps": fps,
    }


def extract_audio_wav(video_path: Path, output_wav: Path) -> Path:
    """Extract a 16 kHz mono 16-bit PCM WAV from a video."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_wav),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return output_wav


def measure_audio_levels(audio_path: Path) -> tuple[float | None, float | None]:
    """Return (mean_dbfs, max_dbfs) from ffmpeg volumedetect."""
    cmd = ["ffmpeg", "-i", str(audio_path), "-af", "volumedetect", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    mean_db = max_db = None
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                mean_db = float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except ValueError:
                pass
        elif "max_volume:" in line:
            try:
                max_db = float(line.split("max_volume:")[1].split("dB")[0].strip())
            except ValueError:
                pass
    return mean_db, max_db


def measure_energy_percentiles(audio_path: Path) -> tuple[float, float]:
    """Calculate the 10th percentile (noise floor) and 90th percentile (speech level) in dBFS."""
    with wave.open(str(audio_path), "rb") as handle:
        sample_rate = handle.getframerate()
        n_samples = handle.getnframes()
        raw = handle.readframes(n_samples)
    pcm = array.array("h")
    pcm.frombytes(raw)
    if sys.byteorder != "little":
        pcm.byteswap()

    window_size = int(sample_rate * 0.05)
    if window_size <= 0:
        return -50.0, -20.0
    energies: list[float] = []
    for offset in range(0, len(pcm) - window_size, window_size):
        chunk = pcm[offset : offset + window_size]
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
        db = 20.0 * math.log10(rms / 32768.0) if rms > 0 else -100.0
        energies.append(db)
    if not energies:
        return -50.0, -20.0
    energies.sort()
    p10 = energies[int(len(energies) * 0.10)]
    p90 = energies[int(len(energies) * 0.90)]
    return p10, p90
