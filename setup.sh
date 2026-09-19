#!/bin/bash
# video-editor — one-time setup
# Run once after installing the skill: bash setup.sh
# Requirements: macOS / Linux, ffmpeg, Python 3.10+

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SKILL_DIR"

echo "=== video-editor Setup ==="
echo "Skill directory: $SKILL_DIR"
echo ""

# 1. Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo "ERROR: ffmpeg not found. Please install ffmpeg (e.g. brew install ffmpeg)."
    exit 1
fi
echo "[1/3] ffmpeg: $(ffmpeg -version 2>&1 | head -1 | cut -d' ' -f1-3) OK"

# 2. Check bl CLI
if command -v bl &>/dev/null; then
    echo "[2/3] bl CLI: $(bl --version 2>/dev/null || echo 'OK')"
else
    echo "[2/3] bl CLI not found. Will require DASHSCOPE_API_KEY environment variable."
fi

# 3. Setup Python venv with silero-vad
echo "[3/3] Setting up Python environment..."
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet silero-vad torch

"$SKILL_DIR/.venv/bin/python3" -c "import silero_vad; print('silero-vad verified.')"

echo ""
echo "=== Setup complete ==="
echo "video-editor is ready to use with .venv/bin/python3."
