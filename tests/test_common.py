#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

# Add scripts dir to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from common import (
    complement_intervals,
    format_timestamp,
    intersection_duration,
    interval_duration,
    merge_intervals,
)


class CommonIntervalTests(unittest.TestCase):
    def test_merge_intervals_overlapping(self):
        intervals = [(1000.0, 2500.0), (2000.0, 3000.0), (5000.0, 6000.0)]
        merged = merge_intervals(intervals)
        self.assertEqual(merged, [(1000.0, 3000.0), (5000.0, 6000.0)])

    def test_merge_intervals_with_gap(self):
        intervals = [(1000.0, 2000.0), (2050.0, 3000.0)]
        # With gap_ms = 60ms, they should merge
        merged = merge_intervals(intervals, gap_ms=60.0)
        self.assertEqual(merged, [(1000.0, 3000.0)])

    def test_complement_intervals_basic(self):
        cuts = [(1000.0, 2000.0), (4000.0, 5000.0)]
        duration_ms = 6000.0
        kept = complement_intervals(cuts, duration_ms)
        self.assertEqual(kept, [(0.0, 1000.0), (2000.0, 4000.0), (5000.0, 6000.0)])

    def test_complement_intervals_cut_at_start_and_end(self):
        cuts = [(0.0, 1000.0), (5000.0, 6000.0)]
        duration_ms = 6000.0
        kept = complement_intervals(cuts, duration_ms)
        self.assertEqual(kept, [(1000.0, 5000.0)])

    def test_interval_duration_and_intersection(self):
        a = [(0.0, 2000.0), (4000.0, 6000.0)]
        b = [(1000.0, 5000.0)]
        self.assertEqual(interval_duration(a), 4000.0)
        self.assertEqual(interval_duration(b), 4000.0)
        # Intersections: [1000, 2000] (1000ms) and [4000, 5000] (1000ms) = 2000ms
        self.assertEqual(intersection_duration(a, b), 2000.0)

    def test_format_timestamp(self):
        self.assertEqual(format_timestamp(0.0), "00:00.000")
        self.assertEqual(format_timestamp(65.432), "01:05.432")
        self.assertEqual(format_timestamp(3661.5), "61:01.500")


if __name__ == "__main__":
    unittest.main()
