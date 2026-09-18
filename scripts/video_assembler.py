#!/usr/bin/env python3
"""Video assembly and slicing engine using ffmpeg with hardware acceleration and audio micro-fading."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from common import format_timestamp, log, merge_intervals


def detect_video_encoder() -> tuple[str, list[str]]:
    """Select best hardware/software encoder for the current platform."""
    if platform.system() == "Darwin":
        # macOS VideoToolbox hardware acceleration
        return "h264_videotoolbox", ["-b:v", "12M", "-allow_sw", "1"]
    # Fallback to software x264
    return "libx264", ["-crf", "19", "-preset", "fast"]


def assemble_video_encode(
    input_video: Path,
    kept_intervals: list[tuple[float, float]],
    output_video: Path,
    *,
    crossfade_ms: float = 15.0,
) -> Path:
    """Frame-accurate video assembly with VideoToolbox hardware encoding and audio micro-fades."""
    if not kept_intervals:
        raise RuntimeError("No kept intervals specified — cannot assemble empty video.")

    output_video.parent.mkdir(parents=True, exist_ok=True)
    encoder, enc_opts = detect_video_encoder()
    fade_s = crossfade_ms / 1000.0

    # Build ffmpeg filter_complex
    v_filters = []
    a_filters = []
    concat_inputs = []

    for idx, (start_ms, end_ms) in enumerate(kept_intervals):
        start_s = start_ms / 1000.0
        end_s = end_ms / 1000.0
        dur_s = end_s - start_s
        if dur_s <= 0.05:
            continue

        v_label = f"v{idx}"
        a_label = f"a{idx}"

        v_filters.append(
            f"[0:v]trim=start={start_s:.3f}:end={end_s:.3f},setpts=PTS-STARTPTS[{v_label}]"
        )

        # Audio micro-fade at cut boundaries (15ms fade-in, 15ms fade-out) to eliminate jump-cut clicks
        if dur_s > 2 * fade_s:
            fade_out_start = dur_s - fade_s
            a_filter = (
                f"[0:a]atrim=start={start_s:.3f}:end={end_s:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:ss=0:d={fade_s:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade_s:.3f}[{a_label}]"
            )
        else:
            a_filter = f"[0:a]atrim=start={start_s:.3f}:end={end_s:.3f},asetpts=PTS-STARTPTS[{a_label}]"
        a_filters.append(a_filter)
        concat_inputs.append(f"[{v_label}][{a_label}]")

    n_slices = len(concat_inputs)
    if n_slices == 0:
        raise RuntimeError("All kept intervals were shorter than 50ms.")

    concat_str = "".join(concat_inputs) + f"concat=n={n_slices}:v=1:a=1[outv][outa]"
    filter_complex = ";".join(v_filters + a_filters + [concat_str])

    # Write filter_complex to a temp file to avoid OS command line length limits
    with tempfile.NamedTemporaryFile(mode="w", suffix=".filter", delete=False) as f:
        f.write(filter_complex)
        filter_file = Path(f.name)

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(input_video),
            "-filter_complex_script", str(filter_file),
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", encoder,
            *enc_opts,
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_video),
        ]
        log(f"Assembling {n_slices} slice(s) with {encoder} and {crossfade_ms}ms micro-fades...")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg assembly failed:\n{result.stderr[-2000:]}")
    finally:
        if filter_file.exists():
            filter_file.unlink()

    log(f"✅ Successfully exported edited video: {output_video}")
    return output_video


def assemble_video_stream_copy(
    input_video: Path,
    kept_intervals: list[tuple[float, float]],
    output_video: Path,
) -> Path:
    """Fast stream-copy assembly without re-encoding (snapped to keyframes)."""
    output_video.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="video_editor_copy_"))
    concat_txt = temp_dir / "concat.txt"
    slice_files = []

    try:
        for idx, (start_ms, end_ms) in enumerate(kept_intervals):
            start_s = start_ms / 1000.0
            dur_s = (end_ms - start_ms) / 1000.0
            slice_path = temp_dir / f"slice_{idx:04d}.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", f"{start_s:.3f}",
                "-i", str(input_video),
                "-t", f"{dur_s:.3f}",
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(slice_path),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            slice_files.append(slice_path)

        with open(concat_txt, "w") as f:
            for s in slice_files:
                f.write(f"file '{s.resolve()}'\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_video),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    log(f"✅ Fast stream-copy exported: {output_video}")
    return output_video


def generate_markdown_report(
    input_video: Path,
    output_video: Path,
    original_duration_s: float,
    new_duration_s: float,
    pauses: list[dict[str, Any]],
    semantic_cuts: list[dict[str, Any]],
    report_md_path: Path,
) -> Path:
    """Write human-readable editing audit report."""
    saved_s = original_duration_s - new_duration_s
    pct = (saved_s / original_duration_s * 100.0) if original_duration_s else 0.0

    lines = [
        f"# 视频智能剪辑报告",
        f"",
        f"- **源视频**：`{input_video.name}`",
        f"- **输出视频**：`{output_video.name}`",
        f"- **原始时长**：{format_timestamp(original_duration_s)} ({original_duration_s:.1f}s)",
        f"- **剪辑后时长**：{format_timestamp(new_duration_s)} ({new_duration_s:.1f}s)",
        f"- **精简时长**：{format_timestamp(saved_s)} ({saved_s:.1f}s, 减少 **{pct:.1f}%**)",
        f"- **切除停顿数**：{len(pauses)} 处",
        f"- **切除口误/废案数**：{len(semantic_cuts)} 处",
        f"",
        f"---",
        f"",
        f"## 1. 语义口误与废案删减清单",
        f"",
    ]
    if not semantic_cuts:
        lines.append("*未发现明显语义口误或重复废案。*")
    else:
        lines.append("| 序号 | 时间区间 | 类型 | 删减内容 | 替换/保留说明 | 理由 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for idx, c in enumerate(semantic_cuts, start=1):
            s_ts = format_timestamp(c["start_ms"] / 1000.0)
            e_ts = format_timestamp(c["end_ms"] / 1000.0)
            cat = c.get("category", "cleanup")
            rem = c.get("removed_text", "").replace("\n", " ")
            if len(rem) > 25:
                rem = rem[:25] + "..."
            kept = c.get("kept_text", "").replace("\n", " ")
            if len(kept) > 20:
                kept = kept[:20] + "..."
            reason = c.get("reason", "")
            lines.append(f"| {idx} | `{s_ts} → {e_ts}` | `{cat}` | {rem} | {kept or '*(直接顺接)*'} | {reason} |")

    lines.extend([
        f"",
        f"---",
        f"",
        f"## 2. 停顿与静音切除概览",
        f"",
        f"共切除 **{len(pauses)}** 处无声停顿与微换气（句尾门限 350ms，词间门限 450ms，保留 180ms 自然过渡衬垫，切口已应用 15ms 微淡化防爆音）。",
        f"",
    ])

    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_md_path
