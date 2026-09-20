#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

# Add scripts dir to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from silence_detector import (
    detect_adaptive_pauses,
    protect_words_from_cuts,
    resolve_silence_db,
    split_retained_pause,
)


class SilenceDetectorTests(unittest.TestCase):
    def test_resolve_silence_db_explicit(self):
        self.assertEqual(resolve_silence_db("-25", -20.0, -45.0, -15.0), -25.0)

    def test_resolve_silence_db_auto(self):
        # noise_floor = -45.0, speech = -15.0 -> -45 + 0.45 * 30 = -31.5
        db = resolve_silence_db("auto", -20.0, -45.0, -15.0)
        self.assertEqual(db, -31.5)

    def test_split_retained_pause(self):
        kb, ka = split_retained_pause(180.0)
        # total_ms = 180ms
        # keep_after = min(80, 180 * 0.33) = 59.4ms -> 0.0594s
        # keep_before = 180 - 59.4 = 120.6ms -> 0.1206s
        self.assertAlmostEqual(kb + ka, 0.180)
        self.assertGreater(kb, ka)  # Brisk entry: keep less after cut (before next word)

    def test_protect_words_from_cuts(self):
        words = [
            {"word": "测试", "start": 1.0, "end": 2.0},  # 1000ms - 2000ms
        ]
        # Cut is 0.5s to 2.5s (500ms to 2500ms), which completely encompasses the word
        cuts = [
            {"start_ms": 500.0, "end_ms": 2500.0, "source": "silence"},
        ]
        # pad_ms = 60ms: protected word interval is 940ms to 2060ms
        # Cut should be split into [500, 940] (440ms) and [2060, 2500] (440ms)
        protected = protect_words_from_cuts(cuts, words, pad_ms=60.0, min_cut_ms=100.0)
        self.assertEqual(len(protected), 2)
        self.assertAlmostEqual(protected[0]["start_ms"], 500.0)
        self.assertAlmostEqual(protected[0]["end_ms"], 940.0)
        self.assertAlmostEqual(protected[1]["start_ms"], 2060.0)
        self.assertAlmostEqual(protected[1]["end_ms"], 2500.0)

    def test_detect_adaptive_pauses_uses_one_300ms_threshold(self):
        words = [
            {"word": "大家好", "start": 0.0, "end": 1.0},
            {"word": "下一个话题", "start": 1.32, "end": 2.0},  # 320ms pause qualifies
        ]
        silence_regions = [
            (1.0, 1.32),
        ]
        pauses = detect_adaptive_pauses(
            silence_regions,
            words,
            threshold_ms=300.0,
            sentence_threshold_ms=300.0,
            min_pause_ms=180.0,
        )
        self.assertEqual(len(pauses), 1)
        self.assertFalse(pauses[0]["is_sentence_boundary"])

        self.assertEqual(
            detect_adaptive_pauses(
                [(1.0, 1.29)], words,
                threshold_ms=300.0,
                sentence_threshold_ms=300.0,
                min_pause_ms=180.0,
            ),
            [],
        )

    def test_audio_silence_overlap_with_asr_word_does_not_veto_cut(self):
        pauses = detect_adaptive_pauses(
            [(1.0, 1.4)],
            [{"word": "轻声", "start": 1.1, "end": 1.2}],
            threshold_ms=300.0,
            sentence_threshold_ms=300.0,
            min_pause_ms=180.0,
        )
        self.assertEqual(len(pauses), 1)


if __name__ == "__main__":
    unittest.main()
