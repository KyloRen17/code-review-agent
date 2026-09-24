#!/usr/bin/env bash
# 一键 Mock 演示：无需任何 API Key，从本地 diff 到 Markdown 报告
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .venv/Scripts/python ]; then PY=.venv/Scripts/python; else PY=.venv/bin/python; fi

echo "== 1/3 审查含已知缺陷的 diff（examples/buggy.diff）=="
"$PY" -m code_review_agent.cli review examples/buggy.diff

echo
echo "== 2/3 审查干净 diff（examples/clean.diff）=="
"$PY" -m code_review_agent.cli review examples/clean.diff

echo
echo "== 3/3 最新报告预览 =="
LATEST=$(ls -t runs/*/report.md | head -1)
echo "报告路径: $LATEST"
head -40 "$LATEST"
