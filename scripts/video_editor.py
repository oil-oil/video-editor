#!/usr/bin/env python3
"""Unified CLI entrypoint for video-editor."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from audio_extractor import (
    extract_audio_wav,
    measure_audio_levels,
    measure_energy_percentiles,
    probe_video_info,
)
from common import (
    complement_intervals,
    format_timestamp,
    interval_duration,
    load_json,
    log,
    merge_intervals,
    write_json,
)
from semantic_planner import (
    plan_video_cuts,
    refine_cut_boundaries_to_minima,
)
from silence_detector import (
    detect_adaptive_pauses,
    detect_nonspeech_regions_vad,
    detect_silence_regions,
    flatten_words,
    resolve_silence_db,
)
from transcriber import load_api_key, transcribe_audio_bailian
from video_assembler import (
    assemble_video_encode,
    assemble_video_stream_copy,
    generate_markdown_report,
)

CONFIG_PATH = Path.home() / ".config" / "video-editor" / "config.json"
DEFAULT_CONFIG = {
    "model": "qwen3.8-omni-flash",
    "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "pause_threshold_ms": 450.0,
    "sentence_threshold_ms": 350.0,
    "min_pause_ms": 180.0,
    "crossfade_ms": 15.0,
    "concurrency": 5,
}


def get_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            user_cfg = load_json(CONFIG_PATH)
            if isinstance(user_cfg, dict):
                cfg.update(user_cfg)
        except Exception:
            pass
    return cfg


def run_pipeline(
    video_path: Path,
    output_video: Path | None = None,
    *,
    dry_run: bool = False,
    fast_copy: bool = False,
    model: str | None = None,
    pause_threshold_ms: float | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Full intelligent video editing pipeline."""
    video_path = video_path.resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cfg = get_config()
    model = model or cfg.get("model", "qwen3.8-omni-flash")
    api_base = cfg.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    concurrency = int(cfg.get("concurrency", 5))
    pause_thresh = float(pause_threshold_ms or cfg.get("pause_threshold_ms", 450.0))
    sentence_thresh = float(cfg.get("sentence_threshold_ms", 350.0))
    min_pause = float(cfg.get("min_pause_ms", 180.0))
    crossfade_ms = float(cfg.get("crossfade_ms", 15.0))

    if output_video is None:
        stem = video_path.stem
        output_video = video_path.with_name(f"{stem}_edited{video_path.suffix}")
    output_video = output_video.resolve()

    if work_dir is None:
        work_dir = video_path.parent / f".{video_path.stem}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    log(f"🎬 Starting video editing pipeline for: {video_path.name}")
    start_time = time.time()

    # Step 1: Probe video info & extract audio WAV
    info = probe_video_info(video_path)
    original_duration_s = float(info["duration_s"])
    log(f"Video duration: {format_timestamp(original_duration_s)} ({original_duration_s:.1f}s)")

    audio_wav = work_dir / "audio_16k.wav"
    if not audio_wav.exists():
        log("Extracting audio stream to 16kHz mono WAV...")
        extract_audio_wav(video_path, audio_wav)

    # Step 2: Audio levels & percentiles
    mean_db, max_db = measure_audio_levels(audio_wav)
    noise_floor, speech_level = measure_energy_percentiles(audio_wav)
    silence_db = resolve_silence_db("auto", mean_db, noise_floor, speech_level)
    log(f"Calibrated silence threshold: {silence_db} dBFS (noise floor: {noise_floor:.1f} dBFS)")

    # Step 3: Transcription (Bailian)
    transcript_json = work_dir / "transcript.json"
    segments = transcribe_audio_bailian(audio_wav, transcript_json)
    words = flatten_words(segments)
    log(f"ASR complete: {len(segments)} sentences, {len(words)} words.")

    # Step 4: Silence & VAD Detection with Adaptive Pause Threshold
    raw_silences = detect_silence_regions(audio_wav, noise_db=silence_db, min_dur=0.25)
    vad_regions = detect_nonspeech_regions_vad(audio_wav, min_dur=0.25)
    silences_ms = [(s * 1000.0, e * 1000.0) for s, e in (raw_silences + vad_regions)]
    merged_silence_ms = merge_intervals(silences_ms, gap_ms=40.0)
    all_silence = [(s / 1000.0, e / 1000.0) for s, e in merged_silence_ms]

    pause_cuts = detect_adaptive_pauses(
        all_silence,
        words,
        threshold_ms=pause_thresh,
        sentence_threshold_ms=sentence_thresh,
        min_pause_ms=min_pause,
    )
    log(f"Detected {len(pause_cuts)} pause cut(s).")

    # Step 5: Global Semantic Paper Edit Planner (LLM)
    api_key = load_api_key()
    semantic_cuts = plan_video_cuts(
        segments,
        api_key,
        model=model,
        api_base=api_base,
        concurrency=concurrency,
    )

    # Step 6: Refine semantic cuts to waveform minima
    semantic_cuts = refine_cut_boundaries_to_minima(semantic_cuts, audio_wav)

    # Combine all cuts
    all_cut_intervals = [(c["start_ms"], c["end_ms"]) for c in pause_cuts] + [
        (c["start_ms"], c["end_ms"]) for c in semantic_cuts
    ]
    merged_cut_intervals = merge_intervals(all_cut_intervals, gap_ms=50.0)

    # Calculate kept intervals
    duration_ms = original_duration_s * 1000.0
    kept_intervals = complement_intervals(merged_cut_intervals, duration_ms)
    new_duration_ms = interval_duration(kept_intervals)
    new_duration_s = new_duration_ms / 1000.0
    saved_s = original_duration_s - new_duration_s

    log(f"--- Editing Plan Summary ---")
    log(f"Original: {original_duration_s:.1f}s -> Projected: {new_duration_s:.1f}s (Saved: {saved_s:.1f}s, {(saved_s/original_duration_s*100):.1f}%)")
    log(f"Cuts: {len(pause_cuts)} pauses, {len(semantic_cuts)} semantic mistake(s)")

    report_md_path = work_dir / f"{video_path.stem}_edit_report.md"
    generate_markdown_report(
        video_path,
        output_video,
        original_duration_s,
        new_duration_s,
        pause_cuts,
        semantic_cuts,
        report_md_path,
    )
    log(f"Saved audit report: {report_md_path}")

    # Step 7: Video Assembly (unless dry-run)
    if not dry_run:
        if fast_copy:
            assemble_video_stream_copy(video_path, kept_intervals, output_video)
        else:
            assemble_video_encode(video_path, kept_intervals, output_video, crossfade_ms=crossfade_ms)
    else:
        log("Dry-run mode: video assembly skipped.")

    elapsed = time.time() - start_time
    log(f"✨ Pipeline completed in {elapsed:.1f}s!")

    return {
        "input_video": str(video_path),
        "output_video": str(output_video) if not dry_run else None,
        "original_duration_s": original_duration_s,
        "new_duration_s": new_duration_s,
        "saved_s": saved_s,
        "saved_ratio": (saved_s / original_duration_s) if original_duration_s else 0.0,
        "pause_cuts_count": len(pause_cuts),
        "semantic_cuts_count": len(semantic_cuts),
        "report_md": str(report_md_path),
        "dry_run": dry_run,
        "elapsed_s": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description="video-editor: Intelligent Talking-Head Video Auto-Cutter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # cut command
    cut_parser = subparsers.add_parser("cut", help="Cut pauses, stumbles, and restarts from video")
    cut_parser.add_argument("video", type=Path, help="Path to input video file (.mp4, .mov, .mkv)")
    cut_parser.add_argument("-o", "--output", type=Path, help="Path to output edited video file")
    cut_parser.add_argument("--dry-run", action="store_true", help="Analyze and generate plan/report without exporting video")
    cut_parser.add_argument("--fast-copy", action="store_true", help="Fast stream-copy cut without re-encoding (snaps to keyframes)")
    cut_parser.add_argument("--model", type=str, help="AI reasoning model (default: qwen3.8-omni-flash)")
    cut_parser.add_argument("--pause-threshold", type=float, help="Pause threshold in ms (default: 450)")
    cut_parser.add_argument("--work-dir", type=Path, help="Custom directory to store intermediate files")

    # review command (alias to dry-run)
    review_parser = subparsers.add_parser("review", help="Review cuts and generate markdown report without modifying video")
    review_parser.add_argument("video", type=Path, help="Path to input video file")
    review_parser.add_argument("--model", type=str, help="AI reasoning model")
    review_parser.add_argument("--work-dir", type=Path, help="Custom directory to store intermediate files")

    args = parser.parse_args()

    if args.command == "review":
        res = run_pipeline(
            args.video,
            dry_run=True,
            model=args.model,
            work_dir=args.work_dir,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "cut":
        res = run_pipeline(
            args.video,
            output_video=args.output,
            dry_run=args.dry_run,
            fast_copy=args.fast_copy,
            model=args.model,
            pause_threshold_ms=args.pause_threshold,
            work_dir=args.work_dir,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
