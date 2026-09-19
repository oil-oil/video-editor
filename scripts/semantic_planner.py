#!/usr/bin/env python3
"""AI semantic paper edit planner using sliding-window LLM reasoning."""

from __future__ import annotations

import array
import json
import math
import os
import re
import sys
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from common import format_timestamp, log, merge_intervals

END_PUNCTUATION = re.compile(r"[。！？!?；;]$")
SOFT_PUNCTUATION = re.compile(r"[，,：:]$")


def _word_text(word: dict[str, Any]) -> str:
    return str(word.get("word") or word.get("text") or "").strip()


def transcript_atoms(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split ASR sentences into clause-level atomic utterances on punctuation."""
    atoms: list[dict[str, Any]] = []
    for segment in segments:
        words = [
            w
            for w in (segment.get("words") or [])
            if w.get("start") is not None and w.get("end") is not None and _word_text(w)
        ]
        if not words:
            text = str(segment.get("text") or "").strip()
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            if text and end > start:
                atoms.append({"start": start, "end": end, "text": text})
            continue

        current: list[dict[str, Any]] = []
        for index, word in enumerate(words):
            current.append(word)
            text = "".join(_word_text(item) for item in current)
            start = float(current[0]["start"])
            end = float(current[-1]["end"])
            next_start = (
                float(words[index + 1]["start"]) if index + 1 < len(words) else None
            )
            token = _word_text(word)
            hard_boundary = bool(END_PUNCTUATION.search(token))
            soft_boundary = bool(SOFT_PUNCTUATION.search(token))
            gap_boundary = next_start is not None and next_start - end >= 0.45
            duration_boundary = end - start >= 8.0
            if hard_boundary or soft_boundary or gap_boundary or duration_boundary:
                atoms.append({"start": start, "end": end, "text": text})
                current = []
        if current:
            atoms.append({
                "start": float(current[0]["start"]),
                "end": float(current[-1]["end"]),
                "text": "".join(_word_text(item) for item in current),
            })

    atoms.sort(key=lambda item: (item["start"], item["end"]))
    for index, atom in enumerate(atoms, start=1):
        atom["id"] = f"U{index:04d}"
    return atoms


def chunk_transcript_atoms(
    atoms: list[dict[str, Any]],
    target_duration: float = 210.0,
    overlap: float = 35.0,
) -> list[list[dict[str, Any]]]:
    """Split transcript atoms into overlapping windows aligned to natural speech gaps."""
    if not atoms:
        return []
    total_start = float(atoms[0]["start"])
    total_end = float(atoms[-1]["end"])
    if total_end - total_start <= target_duration:
        return [atoms]

    chunks: list[list[dict[str, Any]]] = []
    start_idx = 0
    n = len(atoms)
    while start_idx < n:
        chunk_start_time = float(atoms[start_idx]["start"])
        target_end_time = chunk_start_time + target_duration
        if target_end_time >= total_end:
            chunks.append(atoms[start_idx:])
            break

        best_end_idx = start_idx
        best_score = float("inf")
        for i in range(start_idx, n):
            cur_end = float(atoms[i]["end"])
            next_start = float(atoms[i + 1]["start"]) if i + 1 < n else cur_end
            gap = next_start - cur_end
            dist = abs(cur_end - target_end_time)
            score = dist - (15.0 if gap >= 0.8 else (5.0 if gap >= 0.4 else 0.0))
            if cur_end >= target_end_time - 30.0 and score < best_score:
                best_score = score
                best_end_idx = i
            if cur_end > target_end_time + 30.0:
                break

        chunk = atoms[start_idx : best_end_idx + 1]
        chunks.append(chunk)

        actual_end_time = float(chunk[-1]["end"])
        next_target_start = actual_end_time - overlap
        next_start_idx = best_end_idx
        for j in range(best_end_idx, start_idx, -1):
            if float(atoms[j]["start"]) <= next_target_start:
                next_start_idx = j
                break
        if next_start_idx <= start_idx:
            next_start_idx = best_end_idx + 1
        start_idx = next_start_idx

    return chunks


def build_prompt(atoms: list[dict[str, Any]]) -> str:
    rows = []
    for i, item in enumerate(atoms):
        if i > 0:
            gap = float(item["start"]) - float(atoms[i - 1]["end"])
            if gap >= 1.5:
                rows.append(f"--- [PAUSE {gap:.1f}s] ---")
        rows.append(
            f"[{item['id']} {format_timestamp(item['start'])}-{format_timestamp(item['end'])}] {item['text']}"
        )
    transcript_rows = "\n".join(rows)
    return f"""You are making a PAPER EDIT for a spoken video tutorial.
The full transcript is below as timestamped atomic utterances with explicit [PAUSE Xs] markers.
Find every plausible contiguous range that is a recording mistake, while preserving the
creator's intended explanation.

This is candidate generation, not final deletion. Favor recall, but every
candidate still needs concrete structural evidence.
Rely on transcript structure, spoken cadence, and explicit pauses.

Include:
- an abandoned or stumbled earlier take followed by a clean restart;
- an extended preliminary or rambling attempt (10-60s) with long pauses (notice [PAUSE Xs] markers) where the speaker hesitates, trials an incomplete explanation, and restarts/restructures the explanation from scratch; propose the preliminary attempt before the clean restart as an abandoned_take, with replacement_ids set to the clean restart take;
- immediate repeated words, stuttered syllables, or delivery stumbles (e.g. speaker stumbles "这个平台他们" right before "他们就是提供..."; propose the stumble as delivery_cleanup);
- local self-correction or slip-of-the-tongue where words are immediately superseded (e.g. speaker says "就是这个速转快。" then immediately corrects to "就这个转速快，然后..."; propose the slip as self_correction with replacement set to the corrected utterance);
- false starts or aborted sentence lead-ins where the speaker starts a thought, abandons it, and restarts a different phrasing (e.g. "不过你最好是，" immediately followed by "不过这个门槛就比较高了"; propose the false start as delivery_cleanup or abandoned_take);
- explicit instruction to restart, recording meta-talk, accidental live utterances, or off-topic remarks (e.g. telling pets to go away, personal subscription expiring comments, UI loading mutterings, premature outro remarks) that do not belong to the final tutorial;
- an earlier duplicate take whose intended information is fully present in a later cleaner take;
- a short dangling connector, repeated syllable, hesitation, or delivery fragment whose removal makes the surrounding spoken sentence more fluent without losing a claim.

Do NOT include:
- fluent discourse markers merely because they are short;
- fluent finalized explanations that are part of the intended tutorial;
- a repeated passage that adds a claim, example, number, warning, or troubleshooting detail;
- stylistic shortening without evidence of a recording mistake.

Return strict JSON only in this shape:
{{
  "edits": [
    {{
      "remove_start_id": "U0001",
      "remove_end_id": "U0002",
      "cut_until_id": "U0003 or null",
      "replacement_ids": [
        "U0010"
      ],
      "removed_quote": "verbatim words copied from the removed IDs",
      "replacement_quote": "verbatim words copied from replacement IDs",
      "category": "abandoned_take | explicit_restart | duplicate_take | self_correction | delivery_cleanup | recording_meta",
      "confidence": "high | medium | low",
      "reason": "specific evidence that the range is disposable"
    }}
  ]
}}

FULL TRANSCRIPT:
{transcript_rows}
"""


def extract_json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    match = re.search(r"(\{[\s\S]*\})", text)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    return {"edits": []}


def call_chat_completion(
    api_base: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int = 120,
    enable_thinking: bool = False,
) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a meticulous video paper editor. Return strict JSON only. Keep reason brief (under 20 words).",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "stream": True,
        "enable_thinking": enable_thinking,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        full_text = []
        for line in response:
            line_str = line.decode("utf-8").strip()
            if not line_str.startswith("data:"):
                continue
            body = line_str[5:].strip()
            if body == "[DONE]":
                break
            try:
                chunk = json.loads(body)
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    full_text.append(content)
            except Exception:
                pass
        raw_text = "".join(full_text)
    return extract_json_from_text(raw_text)


def grounding_text(text: str) -> str:
    return re.sub(r"[\s\.,!?;:\"'。！？!?；;，,：：“”'、…—\-_]+", "", text)


def candidates_from_plan(
    plan: dict[str, Any],
    atoms: list[dict[str, Any]],
    model: str,
) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in atoms}
    positions = {item["id"]: idx for idx, item in enumerate(atoms)}
    candidates = []

    for raw in plan.get("edits") or []:
        start_id = raw.get("remove_start_id")
        end_id = raw.get("remove_end_id")
        if start_id not in by_id or end_id not in by_id:
            continue
        if positions[start_id] > positions[end_id]:
            continue

        start = float(by_id[start_id]["start"])
        end = float(by_id[end_id]["end"])
        spoken_end = end
        cut_until_id = raw.get("cut_until_id")
        if cut_until_id is None and positions[end_id] + 1 < len(atoms):
            cut_until_id = atoms[positions[end_id] + 1]["id"]
        if cut_until_id and cut_until_id in by_id:
            if positions[cut_until_id] == positions[end_id] + 1:
                end = float(by_id[cut_until_id]["start"])

        category = raw.get("category", "delivery_cleanup")
        confidence = raw.get("confidence", "medium")
        replacement_ids = [
            str(item)
            for item in (raw.get("replacement_ids") or [])
            if str(item) in by_id
            and not (positions[start_id] <= positions[str(item)] <= positions[end_id])
        ]
        duration_ms = (end - start) * 1000.0
        if duration_ms < 500.0:
            continue

        replacementless_local_cleanup = (
            category
            in {
                "self_correction",
                "delivery_cleanup",
                "abandoned_take",
                "explicit_restart",
            }
            and confidence in {"high", "medium"}
            and cut_until_id in by_id
            and positions[cut_until_id] == positions[end_id] + 1
        )
        if (
            category not in {"recording_meta", "screen_pause"}
            and not replacement_ids
            and not replacementless_local_cleanup
        ):
            continue

        removed_atoms = atoms[positions[start_id] : positions[end_id] + 1]
        removed_text = "".join(item["text"] for item in removed_atoms)
        removed_quote_value = str(raw.get("removed_quote") or "").strip()
        gt_removed = grounding_text(removed_text)

        # Grounding check with ellipsis support
        matched = False
        if removed_quote_value and grounding_text(removed_quote_value) in gt_removed:
            matched = True
        elif "..." in removed_quote_value or "…" in removed_quote_value:
            parts = [
                p.strip()
                for p in re.split(r"\.{3,}|…+", removed_quote_value)
                if p.strip()
            ]
            if len(parts) >= 2:
                if (
                    grounding_text(parts[0]) in gt_removed
                    and grounding_text(parts[-1]) in gt_removed
                ):
                    matched = True
        if not matched and len(gt_removed) > 0:
            continue

        replacement_text = "".join(by_id[item]["text"] for item in replacement_ids)
        candidates.append({
            "id": f"cand_{len(candidates) + 1:03d}",
            "start_ms": round(start * 1000.0),
            "end_ms": round(end * 1000.0),
            "duration_ms": round(duration_ms),
            "spoken_start_ms": round(start * 1000.0),
            "spoken_end_ms": round(spoken_end * 1000.0),
            "removed_text": removed_text,
            "kept_text": replacement_text,
            "category": category,
            "confidence": confidence,
            "reason": str(raw.get("reason") or "").strip(),
            "model": model,
        })
    return candidates


def plan_video_cuts(
    segments: list[dict[str, Any]],
    api_key: str,
    *,
    model: str = "qwen3.8-omni-flash",
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    concurrency: int = 5,
    enable_thinking: bool = False,
) -> list[dict[str, Any]]:
    """Generate global paper-edit candidates using sliding windows and concurrency."""
    atoms = transcript_atoms(segments)
    chunks = chunk_transcript_atoms(atoms, target_duration=210.0, overlap=35.0)
    log(
        f"Generated {len(atoms)} clause atoms across {len(chunks)} sliding window(s)."
    )

    all_candidates = []
    if len(chunks) == 1:
        prompt = build_prompt(chunks[0])
        plan = call_chat_completion(
            api_base, api_key, model, prompt, enable_thinking=enable_thinking
        )
        all_candidates = candidates_from_plan(plan, chunks[0], model)
    else:
        log(f"Scanning {len(chunks)} windows with concurrency={concurrency}...")

        def _worker(idx_chunk):
            idx, chunk = idx_chunk
            p = build_prompt(chunk)
            resp = call_chat_completion(
                api_base, api_key, model, p, enable_thinking=enable_thinking
            )
            return candidates_from_plan(resp, chunk, model)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(_worker, (i, c)) for i, c in enumerate(chunks)
            ]
            for f in as_completed(futures):
                try:
                    cands = f.result()
                    all_candidates.extend(cands)
                except Exception as exc:
                    log(f"⚠️  Chunk scan error: {exc}")

    # Deduplicate candidates across overlapping windows
    all_candidates.sort(key=lambda c: (c["start_ms"], c["end_ms"]))
    deduped = []
    for c in all_candidates:
        if not deduped:
            deduped.append(c)
            continue
        prev = deduped[-1]
        # IoU check
        ov = max(
            0.0, min(prev["end_ms"], c["end_ms"]) - max(prev["start_ms"], c["start_ms"])
        )
        union = (
            max(prev["end_ms"], c["end_ms"]) - min(prev["start_ms"], c["start_ms"])
        )
        iou = ov / union if union > 0 else 0.0
        if iou >= 0.5:
            # Keep broader/higher confidence
            if c.get("confidence") == "high" and prev.get("confidence") != "high":
                deduped[-1] = c
        else:
            deduped.append(c)

    log(
        f"AI Semantic Planner: found {len(deduped)} candidate(s) for mistake removal."
    )
    return deduped


def refine_cut_boundaries_to_minima(
    cuts: list[dict[str, Any]],
    audio_path: Path,
    search_window_ms: float = 120.0,
) -> list[dict[str, Any]]:
    """Snap cut start and end to nearby waveform minima / zero-crossings."""
    if not cuts or not audio_path.exists():
        return cuts
    try:
        with wave.open(str(audio_path), "rb") as handle:
            sr = handle.getframerate()
            pcm = array.array("h")
            pcm.frombytes(handle.readframes(handle.getnframes()))
    except Exception:
        return cuts

    refined = []
    max_amp = 32768.0
    half_win = int(sr * (search_window_ms / 1000.0) / 2)

    for c in cuts:
        start_samp = int(c["start_ms"] * sr / 1000.0)
        end_samp = int(c["end_ms"] * sr / 1000.0)

        # Snap start
        s_from = max(0, start_samp - half_win)
        s_to = min(len(pcm), start_samp + half_win)
        best_s = start_samp
        min_s_val = float("inf")
        for i in range(s_from, s_to):
            val = abs(pcm[i])
            if val < min_s_val:
                min_s_val = val
                best_s = i

        # Snap end
        e_from = max(0, end_samp - half_win)
        e_to = min(len(pcm), end_samp + half_win)
        best_e = end_samp
        min_e_val = float("inf")
        for i in range(e_from, e_to):
            val = abs(pcm[i])
            if val < min_e_val:
                min_e_val = val
                best_e = i

        new_c = dict(c)
        new_c["start_ms"] = round(best_s * 1000.0 / sr)
        new_c["end_ms"] = round(best_e * 1000.0 / sr)
        new_c["duration_ms"] = new_c["end_ms"] - new_c["start_ms"]
        refined.append(new_c)
    return refined
