#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

# Add scripts dir to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from semantic_planner import chunk_transcript_atoms, transcript_atoms


class TranscriptAtomsTests(unittest.TestCase):
    def test_transcript_atoms_punctuation_split(self):
        segments = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "大家早上好，今天我们来介绍一个新工具。",
                "words": [
                    {"word": "大家", "start": 0.0, "end": 0.4},
                    {"word": "早上好，", "start": 0.4, "end": 1.0},
                    {"word": "今天", "start": 1.1, "end": 1.4},
                    {"word": "我们", "start": 1.4, "end": 1.7},
                    {"word": "来介绍", "start": 1.7, "end": 2.2},
                    {"word": "一个", "start": 2.2, "end": 2.5},
                    {"word": "新工具。", "start": 2.5, "end": 3.0},
                ],
            }
        ]
        atoms = transcript_atoms(segments)
        self.assertEqual(len(atoms), 2)
        self.assertEqual(atoms[0]["id"], "U0001")
        self.assertEqual(atoms[0]["text"], "大家早上好，")
        self.assertAlmostEqual(atoms[0]["start"], 0.0)
        self.assertAlmostEqual(atoms[0]["end"], 1.0)

        self.assertEqual(atoms[1]["id"], "U0002")
        self.assertEqual(atoms[1]["text"], "今天我们来介绍一个新工具。")
        self.assertAlmostEqual(atoms[1]["start"], 1.1)
        self.assertAlmostEqual(atoms[1]["end"], 3.0)

    def test_transcript_atoms_gap_split(self):
        segments = [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "第一个概念 第二个概念",
                "words": [
                    {"word": "第一个概念", "start": 0.0, "end": 1.0},
                    # 1.0s to 1.8s is 0.8s gap (> 0.45s)
                    {"word": "第二个概念", "start": 1.8, "end": 2.8},
                ],
            }
        ]
        atoms = transcript_atoms(segments)
        self.assertEqual(len(atoms), 2)
        self.assertEqual(atoms[0]["text"], "第一个概念")
        self.assertEqual(atoms[1]["text"], "第二个概念")

    def test_chunk_transcript_atoms(self):
        atoms = [
            {"id": f"U{i:04d}", "start": float(i * 10), "end": float(i * 10 + 5), "text": f"item {i}"}
            for i in range(25)  # 0s to 245s
        ]
        chunks = chunk_transcript_atoms(atoms, target_duration=100.0, overlap=20.0)
        self.assertGreater(len(chunks), 1)
        # Ensure all atoms are covered
        first_chunk_ids = {a["id"] for a in chunks[0]}
        self.assertIn("U0000", first_chunk_ids)
        last_chunk_ids = {a["id"] for a in chunks[-1]}
        self.assertIn("U0024", last_chunk_ids)


if __name__ == "__main__":
    unittest.main()
