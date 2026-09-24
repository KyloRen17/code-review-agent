#!/usr/bin/env bash
# 一键 Mock 演示：无需任何 API Key，覆盖题目六项核心能力
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .venv/Scripts/python ]; then PY=.venv/Scripts/python; else PY=.venv/bin/python; fi
CLI="$PY -m code_review_agent.cli"

echo "=================================================================="
echo "0/6 准备：清理旧演示输出"
echo "=================================================================="
rm -rf runs/demo
mkdir -p runs/demo

echo
echo "=================================================================="
echo "1/6【最小审查闭环 + 安全（脱敏/工具/置信度）】审查 buggy.diff"
echo "=================================================================="
$CLI review examples/buggy.diff --report-dir runs/demo --db runs/demo/cra.sqlite 2>&1 | grep -v "^INFO\|^WARNING" || true
REPORT=$(ls -t runs/demo/*/report.md | head -1)
TASK=$(basename "$(dirname "$REPORT")")
echo
echo "--- 报告头部（预算/脱敏/工具/工作单元统计）---"
grep -E "预算|脱敏|工具|工作单元|高置信|仅供参考" "$REPORT" | head -10
echo
echo "--- 安全检查：原始 secret 不得出现在任何产物中 ---"
if grep -rq "sk-live-9f8e7d6c5b4a3210" runs/demo; then
  echo "!!! LEAK DETECTED"; exit 1
else
  echo "OK: secret 未出现在报告/日志/数据库导出中（diff 中为掩码值）"
fi

echo
echo "=================================================================="
echo "2/6【可观测】trace 查询：finding → LLM 调用 → prompt/响应脱敏快照 → spans"
echo "=================================================================="
FID=$(grep -o "cra trace [0-9a-f]*" "$REPORT" | head -1 | cut -d' ' -f3)
$CLI trace "$FID" --db runs/demo/cra.sqlite --export runs/demo/trace_export.json 2>/dev/null | head -22
echo "..."
echo "导出 JSON: runs/demo/trace_export.json（含 span 时间线与复核信息）"

echo
echo "=================================================================="
echo "3/6【可恢复】resume 幂等：已完成任务不再重跑、不重复发布"
echo "=================================================================="
$CLI resume "$TASK" --db runs/demo/cra.sqlite --report-dir runs/demo 2>/dev/null | tail -1

echo
echo "=================================================================="
echo "4/6【Token/金额预算】高单价 + 低预算 → 部分报告（未审查范围明示）"
echo "=================================================================="
mkdir -p runs/demo/budget
cat > runs/demo/budget/pricing.yaml <<'YAML'
currency: CNY
models:
  mock-reviewer-v1:
    input_per_1k: 100.0
    output_per_1k: 0.0
YAML
cat > runs/demo/budget/agent.yaml <<'YAML'
budget:
  currency: CNY
  limit: 30.0
  pricing_file: runs/demo/budget/pricing.yaml
review:
  recheck: false
YAML
$CLI review examples/buggy.diff --config runs/demo/budget/agent.yaml \
  --report-dir runs/demo/budget --db runs/demo/budget/cra.sqlite 2>&1 | grep -v "^INFO\|^WARNING" | tail -4 || true
BREPORT=$(ls -t runs/demo/budget/*/report.md | head -1)
grep -E "预算|未审查" "$BREPORT" | head -3

echo
echo "=================================================================="
echo "5/6【可扩展 + 工具证据】声明式注册的工具结果（diff-stat / py-ast-check）"
echo "=================================================================="
grep -E "^\- \*\*工具" "$REPORT"
echo "（新增工具 = tools/implementations/ 下新类 + configs/tools.yaml 登记，主流程零改动）"

echo
echo "=================================================================="
echo "6/6【负样本】clean.diff → 未发现可确认问题"
echo "=================================================================="
$CLI review examples/clean.diff --report-dir runs/demo --db runs/demo/cra.sqlite 2>&1 | grep "发现" || true

echo
echo "=================================================================="
echo "演示完成。产物目录: runs/demo/"
echo "  - $REPORT"
echo "  - runs/demo/trace_export.json"
echo "  - $BREPORT（预算部分报告）"
echo "=================================================================="
