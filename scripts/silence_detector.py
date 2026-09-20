#!/usr/bin/env python3
"""Silence and pause detection with VAD, adaptive sentence thresholds, and word protection."""

from __future__ import annotations

import array
import math
import re
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any
from common import log, merge_intervals

_SILERO_MODEL = None
SENTENCE_ENDINGS = {"。", "！", "？", ".", "!", "?", "…", "；", ";", "\n"}


def resolve_silence_db(
    requested: str,
    mean_db: float | None,
    noise_floor: float,
    speech_level: float,
) -> float:
    """Derive adaptive noise threshold from measured percentiles or requested value."""
    if requested != "auto":
        try:
            return float(requested)
        except ValueError:
            pass
    if speech_level > noise_floor:
        threshold = noise_floor + 0.45 * (speech_level - noise_floor)
        return round(max(-45.0, min(-18.0, threshold)), 1)
    if mean_db is not None:
        return round(max(-45.0, min(-18.0, mean_db - 10.0)), 1)
    return -28.0


def detect_silence_regions(
    audio_path: Path,
    noise_db: float = -30.0,
    min_dur: float = 0.25,
) -> list[tuple[float, float]]:
    """Find silent intervals using ffmpeg silencedetect."""
    cmd = [
        "ffmpeg",
        "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    regions: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                current_start = float(line.split("silence_start:")[1].strip())
            except ValueError:
                current_start = None
        elif "silence_end:" in line and current_start is not None:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip())
                if end > current_start:
                    regions.append((current_start, end))
            except ValueError:
                pass
            current_start = None
    return regions


def detect_nonspeech_regions_vad(
    audio_path: Path,
    min_dur: float = 0.25,
    threshold: float = 0.5,
) -> list[tuple[float, float]]:
    """Detect non-speech intervals via Silero VAD."""
    global _SILERO_MODEL
    try:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
    except ImportError:
        log("⚠️  silero-vad unavailable; relying on energy silence only.")
        return []

    if _SILERO_MODEL is None:
        _SILERO_MODEL = load_silero_vad()

    with wave.open(str(audio_path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != 16000:
            return []
        pcm = array.array("h")
        pcm.frombytes(handle.readframes(handle.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    waveform = torch.tensor(pcm, dtype=torch.float32) / 32768.0

    speech = get_speech_timestamps(
        waveform,
        _SILERO_MODEL,
        threshold=threshold,
        sampling_rate=16000,
        min_speech_duration_ms=120,
        min_silence_duration_ms=120,
        speech_pad_ms=70,
        return_seconds=True,
    )
    duration_s = float(len(waveform)) / 16000.0
    nonspeech = []
    cursor = 0.0
    for item in speech:
        start = float(item["start"])
        end = float(item["end"])
        if start - cursor >= min_dur:
            nonspeech.append((cursor, start))
        cursor = max(cursor, end)
    if duration_s - cursor >= min_dur:
        nonspeech.append((cursor, duration_s))
    return nonspeech


def split_retained_pause(min_pause_ms: float) -> tuple[float, float]:
    """Asymmetric padding: keep less at the start of next slice to enter speech briskly."""
    total_ms = max(0.0, min_pause_ms)
    keep_after_ms = min(80.0, total_ms * 0.33)
    keep_before_ms = max(0.0, total_ms - keep_after_ms)
    return keep_before_ms / 1000.0, keep_after_ms / 1000.0


def flatten_words(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            if w.get("start") is not None and w.get("end") is not None:
                words.append(w)
    return sorted(words, key=lambda w: (w["start"], w["end"]))


def protect_words_from_cuts(
    cuts: list[dict[str, Any]],
    words: list[dict[str, Any]],
    pad_ms: float = 60.0,
    min_cut_ms: float = 150.0,
) -> list[dict[str, Any]]:
    """Trim pause cuts so they NEVER clip ASR recognized speech."""
    if not cuts or not words:
        return cuts
    intervals = sorted(
        (w["start"] * 1000.0 - pad_ms, w["end"] * 1000.0 + pad_ms)
        for w in words
        if w.get("start") is not None and w.get("end") is not None
    )
    protected = []
    for cut in cuts:
        pieces = [(cut["start_ms"], cut["end_ms"])]
        for (ws, we) in intervals:
            if we <= cut["start_ms"] or ws >= cut["end_ms"]:
                continue
            new_pieces = []
            for (ps, pe) in pieces:
                if we <= ps or ws >= pe:
                    new_pieces.append((ps, pe))
                    continue
                if ws > ps:
                    new_pieces.append((ps, ws))
                if we < pe:
                    new_pieces.append((we, pe))
            pieces = new_pieces
        for (ps, pe) in pieces:
            if pe - ps >= min_cut_ms:
                piece = dict(cut)
                piece["start_ms"] = ps
                piece["end_ms"] = pe
                protected.append(piece)
    return protected


def detect_adaptive_pauses(
    silence_regions: list[tuple[float, float]],
    words: list[dict[str, Any]],
    threshold_ms: float = 300.0,
    sentence_threshold_ms: float = 300.0,
    min_pause_ms: float = 180.0,
) -> list[dict[str, Any]]:
    """Build pause cuts from measured silence; ASR overlap does not veto audio evidence."""
    min_cut_ms = max(120.0, min(200.0, threshold_ms - min_pause_ms))
    pauses = []

    for s_start, s_end in silence_regions:
        reg_ms = (s_end - s_start) * 1000.0
        # Find preceding word
        prev_w = None
        for w in words:
            if w["end"] <= s_start:
                prev_w = w
            elif w["start"] >= s_end:
                break
        is_sentence = False
        if prev_w is None:
            is_sentence = True
        else:
            w_text = (prev_w.get("word") or "").strip()
            if any(w_text.endswith(p) for p in SENTENCE_ENDINGS):
                is_sentence = True

        effective_thresh = sentence_threshold_ms if is_sentence else threshold_ms
        if reg_ms <= effective_thresh:
            continue

        kb, ka = split_retained_pause(min_pause_ms)
        c_start = s_start + kb
        c_end = s_end - ka
        c_dur = (c_end - c_start) * 1000.0
        if c_dur < min_cut_ms:
            continue

        pauses.append({
            "start_ms": c_start * 1000.0,
            "end_ms": c_end * 1000.0,
            "duration_ms": reg_ms,
            "source": "silence",
            "is_sentence_boundary": is_sentence,
        })

    return pauses
