#!/usr/bin/env python3
"""Common utility functions for video-editor: interval arithmetic, JSON I/O, formatting."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[video-editor] {msg}", file=sys.stderr, flush=True)


def load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path | str, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
