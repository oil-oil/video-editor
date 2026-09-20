#!/usr/bin/env bash
# 统一虚拟环境与凭据入口；离线命令不读取凭据。
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$SKILL_DIR/.venv/bin/python3"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "请先运行：bash \"$SKILL_DIR/setup.sh\"" >&2
    exit 1
fi

if [[ "${1:-}" == "cut" && " $* " != *" --help "* && " $* " != *" -h "* ]]; then
    # 复用已有环境注入或 bl 配置；不输出 Key。
    if ! "$PYTHON_BIN" - "$SKILL_DIR/scripts" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from transcriber import load_api_key
try:
    load_api_key()
except RuntimeError:
    sys.exit(2)
PY
    then
        exec node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" run default -- \
            "$PYTHON_BIN" "$SKILL_DIR/scripts/video_editor.py" "$@"
    fi
fi
exec "$PYTHON_BIN" "$SKILL_DIR/scripts/video_editor.py" "$@"
