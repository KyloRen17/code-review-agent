#!/usr/bin/env bash
# 一键测试：在项目根目录运行所有测试
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .venv/Scripts/python ]; then PY=.venv/Scripts/python; else PY=.venv/bin/python; fi
"$PY" -m pytest tests "$@"
