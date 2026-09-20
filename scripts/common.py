#!/usr/bin/env python3
"""Common utility functions for video-editor: interval arithmetic, JSON I/O, formatting."""

from __future__ import annotations

import json
import hashlib
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[video-editor] {msg}", file=sys.stderr, flush=True)


def load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path | str, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            f.close()
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)


def source_signature(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest()}


def validate_intervals(intervals: list, duration_ms: float) -> list[tuple[float, float]]:
    """Reject malformed plans instead of silently clamping or dropping content."""
    if not math.isfinite(duration_ms) or duration_ms <= 0:
        raise ValueError("视频时长无效")
    result = []
    previous_end = 0.0
    for pair in intervals:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("剪辑区间必须包含起止时间")
        start, end = map(float, pair)
        if not (math.isfinite(start) and math.isfinite(end)
                and previous_end <= start < end <= duration_ms):
            raise ValueError("剪辑区间越界、重叠、乱序或包含无效数值")
        result.append((start, end))
        previous_end = end
    return result


def format_timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000.0))
    minutes, remainder = divmod(millis, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


def merge_intervals(
    intervals: list[tuple[float, float]], *, gap_ms: float = 0.0
) -> list[tuple[float, float]]:
    """Merge overlapping or near-adjacent intervals."""
    ordered = sorted((float(a), float(b)) for a, b in intervals if float(b) > float(a))
    if not ordered:
        return []
    merged: list[list[float]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + gap_ms:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def complement_intervals(
    cut_intervals: list[tuple[float, float]], duration_ms: float
) -> list[tuple[float, float]]:
    """Given intervals to CUT, return the complementary intervals to KEEP."""
    merged_cuts = merge_intervals(cut_intervals)
    kept = []
    cursor = 0.0
    for cs, ce in merged_cuts:
        cs = max(0.0, min(duration_ms, cs))
        ce = max(cs, min(duration_ms, ce))
        if cs > cursor:
            kept.append((cursor, cs))
        cursor = max(cursor, ce)
    if cursor < duration_ms:
        kept.append((cursor, duration_ms))
    return [(s, e) for s, e in kept if e > s]


def interval_duration(intervals: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in merge_intervals(intervals))


def intersection_duration(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> float:
    l_m = merge_intervals(left)
    r_m = merge_intervals(right)
    total = 0.0
    i = j = 0
    while i < len(l_m) and j < len(r_m):
        total += max(0.0, min(l_m[i][1], r_m[j][1]) - max(l_m[i][0], r_m[j][0]))
        if l_m[i][1] <= r_m[j][1]:
            i += 1
        else:
            j += 1
    return total
