#!/usr/bin/env python3
"""Video assembly and slicing engine using ffmpeg with platform-aware encoding and audio micro-fading."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from functools import wraps
from pathlib import Path
from typing import Any

from common import format_timestamp, log, interval_duration, validate_intervals
from audio_extractor import probe_video_info


def validate_output_path(input_video: Path, output_video: Path, overwrite: bool = False) -> None:
    if (input_video.resolve() == output_video.resolve()
            or (output_video.exists() and input_video.samefile(output_video))):
        raise ValueError("输出不能指向源视频，请选择新文件名")
    if output_video.exists() and not overwrite:
        raise FileExistsError("输出文件已存在；请换文件名，或显式使用 --overwrite")


def safe_export(assemble):
    @wraps(assemble)
    def wrapped(input_video, kept_intervals, output_video, *, overwrite=False, **kwargs):
        input_video, output_video = Path(input_video), Path(output_video)
        validate_output_path(input_video, output_video, overwrite)
        info = probe_video_info(input_video)
        kept_intervals = validate_intervals(kept_intervals, float(info["duration_s"]) * 1000)
        if not kept_intervals or not info["has_audio"] or not info["has_video"]:
            raise ValueError("需要非空保留区间及有效音视频轨道")
        output_video.parent.mkdir(parents=True, exist_ok=True)
        # Keep an existing export intact if encoding or validation fails.
        with tempfile.TemporaryDirectory(prefix=".video_export_", dir=output_video.parent) as d:
            target = Path(d) / ("output" + output_video.suffix)
            assemble(input_video, kept_intervals, target, **kwargs)
            actual = probe_video_info(target)
            expected = interval_duration(kept_intervals) / 1000
            if not actual["has_video"] or not actual["has_audio"] or float(actual["duration_s"]) <= 0:
                raise RuntimeError("导出文件缺少有效音视频轨道")
            delta = float(actual["duration_s"]) - expected
            if assemble.__name__ == "assemble_video_encode":
                tolerance = max(0.1, len(kept_intervals) / float(info["fps"]) + 0.05)
                if abs(delta) > tolerance:
                    raise RuntimeError(f"成片时长偏离计划 {delta:.3f}s，未替换输出文件")
            else:
                log(f"流拷贝为近似剪辑：计划 {expected:.3f}s，实测 {actual['duration_s']:.3f}s；可能保留切点附近内容。")
            validate_output_path(input_video, output_video, overwrite)
            if overwrite:
                os.replace(target, output_video)
            else:
                os.link(target, output_video)  # Fails atomically if another run created it.
        log(f"已验收导出：{output_video}")
        return output_video
    return wrapped


def detect_video_encoder() -> tuple[str, list[str]]:
    """Select best hardware/software encoder for the current platform."""
    if platform.system() == "Darwin":
        # macOS VideoToolbox hardware acceleration
        return "h264_videotoolbox", ["-b:v", "12M", "-allow_sw", "1"]
    # Fallback to software x264
    return "libx264", ["-crf", "19", "-preset", "fast"]


@safe_export
def assemble_video_encode(
    input_video: Path,
    kept_intervals: list[tuple[float, float]],
    output_video: Path,
    *,
    crossfade_ms: float = 15.0,
) -> Path:
    """Frame-accurate video assembly with platform-aware encoding and audio micro-fades."""
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

        v_label = f"v{idx}"
        a_label = f"a{idx}"

        v_filters.append(
            f"[0:v]trim=start={start_s:.3f}:end={end_s:.3f},setpts=PTS-STARTPTS[{v_label}]"
        )

        # Audio micro-fade at cut boundaries (15ms fade-in, 15ms fade-out) to eliminate jump-cut clicks
        if fade_s > 0 and dur_s > 2 * fade_s:
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
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        # FFmpeg 9 on Windows removed the long-standing script-file option.
        # Its replacement reads an option value from a file. Keep that path
        # before the inline graph so long videos do not hit Windows command
        # line length limits.
        if result.returncode != 0 and "filter_complex_script" in result.stderr:
            log("当前 FFmpeg 不支持 filter_complex_script，改用文件参数读取滤镜图重试。")
            file_value_cmd = [
                "ffmpeg",
                "-y",
                "-i", str(input_video),
                "-/filter_complex", str(filter_file),
                "-map", "[outv]",
                "-map", "[outa]",
                "-c:v", encoder,
                *enc_opts,
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_video),
            ]
            result = subprocess.run(
                file_value_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                log("当前 FFmpeg 不支持文件参数读取，改用内联滤镜图重试。")
                inline_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i", str(input_video),
                    "-filter_complex", filter_complex,
                    "-map", "[outv]",
                    "-map", "[outa]",
                    "-c:v", encoder,
                    *enc_opts,
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",
                    str(output_video),
                ]
                result = subprocess.run(
                    inline_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg assembly failed:\n{result.stderr[-2000:]}")
    finally:
        if filter_file.exists():
            filter_file.unlink()

    return output_video


@safe_export
def assemble_video_stream_copy(
    input_video: Path,
    kept_intervals: list[tuple[float, float]],
    output_video: Path,
) -> Path:
    """Approximate preview export: keyframe pre-roll may retain extra content."""
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
                # FFmpeg's concat demuxer treats backslashes as escapes. Use
                # forward slashes so absolute Windows paths (C:/...) work too.
                concat_path = s.resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{concat_path}'\n")

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
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_video


def generate_markdown_report(
    input_video: Path,
    output_video: Path,
    original_duration_s: float,
    new_duration_s: float,
    pauses: list[dict[str, Any]],
    semantic_cuts: list[dict[str, Any]],
    report_md_path: Path,
    *,
    config: dict[str, Any] | None = None,
    actual_duration_s: float | None = None,
    fast_copy: bool = False,
    kept_intervals: list[tuple[float, float]] | None = None,
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
        f"- **计划时长**：{format_timestamp(new_duration_s)} ({new_duration_s:.3f}s)",
        f"- **导出状态**：{'已导出' if actual_duration_s is not None else '仅计划，尚未成功导出'}",
        f"- **精简时长**：{format_timestamp(saved_s)} ({saved_s:.1f}s, 减少 **{pct:.1f}%**)",
        f"- **切除停顿数**：{len(pauses)} 处",
        f"- **切除口误/废案数**：{len(semantic_cuts)} 处",
        f"",
        f"---",
        f"",
        f"## 1. 语义口误与废案删减清单",
        f"",
    ]
    if actual_duration_s is not None:
        lines.insert(7, f"- **实测时长**：{actual_duration_s:.3f}s，偏离计划 {actual_duration_s - new_duration_s:+.3f}s")
    if fast_copy:
        lines.insert(7, "- **近似流拷贝**：切点受关键帧限制，可能保留额外内容；未应用音频淡化。")
    if not semantic_cuts:
        lines.append("*未发现明显语义口误或重复废案。*")
    else:
        lines.append("| 序号 | 时间区间 | 类型 | 删减内容 | 替换/保留说明 | 理由 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for idx, c in enumerate(semantic_cuts, start=1):
            s_ts = format_timestamp(c["start_ms"] / 1000.0)
            e_ts = format_timestamp(c["end_ms"] / 1000.0)
            cat = c.get("category", "cleanup")
            rem = c.get("removed_text", "").replace("\n", " ").replace("|", "\\|")
            kept = c.get("kept_text", "").replace("\n", " ").replace("|", "\\|")
            reason = c.get("reason", "").replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {idx} | `{s_ts} → {e_ts}` | `{cat}` | {rem} | {kept or '*(直接顺接)*'} | {reason} |")

    lines.extend([
        f"",
        f"---",
        f"",
        f"## 2. 停顿与静音切除概览",
        f"",
        f"计划压缩 **{len(pauses)}** 处停顿。参数：`{config or {}}`。",
        "音频微淡化已应用。" if actual_duration_s is not None and not fast_copy and (config or {}).get("crossfade_ms", 0) > 0 else "本次未确认应用音频微淡化。",
        f"",
    ])
    if kept_intervals is not None:
        lines.extend(["## 3. 实际采用的保留区间", "",
                      "上方话术与理由来自分析阶段；手动修改计划后，以本表为准。流拷贝仅近似执行这些边界。", "",
                      "| 开始 | 结束 |", "| --- | --- |"])
        lines.extend(f"| {format_timestamp(s / 1000)} | {format_timestamp(e / 1000)} |"
                     for s, e in kept_intervals)

    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_md_path
