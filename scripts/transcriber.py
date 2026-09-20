#!/usr/bin/env python3
"""Transcription service using Alibaba Bailian (bl CLI or DashScope SDK) with word-level timestamps."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from common import load_json, log, write_json


def load_api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if key and key.strip():
        return key.strip()
    bailian_config = Path.home() / ".bailian" / "config.json"
    if bailian_config.exists():
        try:
            key = str(load_json(bailian_config).get("api_key") or "").strip()
            if key:
                return key
        except Exception:
            pass
    raise RuntimeError("未找到百炼凭据，请通过 scripts/run.sh 运行，或先配置安全凭据页。")


def _ms(value: Any) -> float:
    return round(float(value or 0.0) / 1000.0, 3)


def _word_text(word: dict[str, Any]) -> str:
    text = (word.get("text") or word.get("word") or "").strip()
    punctuation = (word.get("punctuation") or "").strip()
    return (text + punctuation).strip()


def _convert_bailian_output(raw_data: dict[str, Any]) -> list[dict[str, Any]]:
    transcripts = raw_data.get("transcripts") or []
    raw_sentences = []
    if transcripts:
        raw_sentences = transcripts[0].get("sentences") or []
    elif "output" in raw_data:
        out = raw_data["output"]
        raw_sentences = (
            out.get("sentence")
            or (out.get("results") or [{}])[0].get("sentences")
            or []
        )
    elif "sentences" in raw_data or "sentence" in raw_data:
        raw_sentences = raw_data.get("sentences") or raw_data.get("sentence") or []
    else:
        raise ValueError("无法识别 ASR 响应格式，已停止剪辑")
    if isinstance(raw_sentences, dict):
        raw_sentences = [raw_sentences]

    segments: list[dict[str, Any]] = []
    for s in raw_sentences:
        text = str(s.get("text") or "").strip()
        if not text:
            continue

        begin = s.get("begin_time", s.get("start_time", s.get("start", 0)))
        end = s.get("end_time", s.get("stop_time", s.get("end", begin)))
        is_ms = any(k in s for k in ("begin_time", "end_time", "start_time", "stop_time"))
        start_s = round(float(begin) / 1000.0, 3) if is_ms else round(float(begin), 3)
        end_s = round(float(end) / 1000.0, 3) if is_ms else round(float(end), 3)

        words: list[dict[str, Any]] = []
        for w in s.get("words") or []:
            token_text = _word_text(w)
            if not token_text:
                continue
            w_begin = w.get("begin_time", w.get("start_time", w.get("start", 0)))
            w_end = w.get("end_time", w.get("stop_time", w.get("end", w_begin)))
            w_is_ms = any(k in w for k in ("begin_time", "end_time", "start_time", "stop_time"))
            w_start_s = round(float(w_begin) / 1000.0, 3) if w_is_ms else round(float(w_begin), 3)
            w_end_s = round(float(w_end) / 1000.0, 3) if w_is_ms else round(float(w_end), 3)
            words.append({"word": token_text, "start": w_start_s, "end": w_end_s})

        segments.append({
            "start": start_s,
            "end": end_s,
            "text": text,
            "words": words,
        })

    segments.sort(key=lambda item: (item["start"], item["end"]))
    return segments


def validate_transcript(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Missing timestamps must never silently disable the word-protection stage.
    if not isinstance(segments, list) or not segments:
        raise ValueError("ASR 未返回有效语音，无法安全自动剪辑；请检查音轨或转录结果")
    for segment in segments:
        words = segment.get("words")
        if not words:
            raise ValueError("ASR 缺少字时间戳，已停止剪辑")
        for item in [segment, *words]:
            start, end = float(item["start"]), float(item["end"])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                raise ValueError("ASR 时间戳无效，已停止剪辑")
    return segments


def transcribe_audio_bailian(
    audio_path: Path,
    output_json: Path,
    *,
    language: str = "zh",
    model: str = "fun-asr",
) -> list[dict[str, Any]]:
    """Transcribe audio with word timestamps via bl CLI or DashScope SDK."""
    if output_json.exists():
        log(f"Reusing cached transcript: {output_json}")
        return validate_transcript(load_json(output_json))

    bl_bin = shutil.which("bl")
    if bl_bin:
        log(f"Transcribing audio with Bailian FunAudio ASR via {bl_bin}...")
        raw_json_path = output_json.with_name(f"{output_json.stem}_raw.json")
        raw_json_path.unlink(missing_ok=True)
        cmd = [
            bl_bin,
            "speech",
            "recognize",
            "--url", str(audio_path.resolve()),
            "--out", str(raw_json_path.resolve()),
            "--language", language,
            "--model", model,
            "--timeout", "600",
            "--quiet",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(f"Bailian CLI recognition failed:\n{result.stderr or result.stdout}")

        if not raw_json_path.exists():
            raise RuntimeError(f"Bailian CLI did not generate output file: {raw_json_path}")

        raw_data = load_json(raw_json_path)
        segments = validate_transcript(_convert_bailian_output(raw_data))
        write_json(output_json, segments)
        log(f"Saved transcript with {len(segments)} sentence(s) to {output_json}")
        return segments

    # Fallback to DashScope python SDK
    api_key = load_api_key()
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except ImportError:
        raise RuntimeError("Neither 'bl' CLI nor 'dashscope' python package is available.")

    dashscope.api_key = api_key
    sdk_model = "paraformer-realtime-v2"
    log(f"Transcribing audio via DashScope SDK ({sdk_model})...")
    recognition = Recognition(
        model=sdk_model,
        format="wav",
        sample_rate=16000,
        callback=None,
    )
    result = recognition.call(str(audio_path.resolve()))
    if result.status_code != 200:
        raise RuntimeError(f"Transcription failed: {result.message} (code {result.status_code})")

    segments = validate_transcript(_convert_bailian_output({"sentence": result.get_sentence()}))
    write_json(output_json, segments)
    log(f"Saved transcript with {len(segments)} sentence(s) to {output_json}")
    return segments
