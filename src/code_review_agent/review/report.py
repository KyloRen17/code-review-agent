from __future__ import annotations

from datetime import datetime, timezone

from ..security.redactor import RedactionReport
from ..tools.base import ToolResult
from .finding import Confidence, Finding, Severity

_SEVERITY_ORDER = [
    Severity.critical,
    Severity.high,
    Severity.medium,
    Severity.low,
    Severity.info,
]


def _status_value(unit) -> str:
    return getattr(getattr(unit, "status", None), "value", "pending") or "pending"


def _finding_section(f: Finding) -> str:
    lines = [f"### [{f.severity.value.upper()}] {f.title}", ""]
    location = f"{f.file}:{f.line}" if f.line is not None else f"{f.file}（未定位到行）"
    lines.append(f"- **位置**: `{location}`")
    if f.evidence:
        lines.append(f"- **证据**:\n  ```\n  {f.evidence}\n  ```")
    lines.append(f"- **说明**: {f.description}")
    if f.trigger:
        lines.append(f"- **触发条件**: {f.trigger}")
    if f.suggestion:
        lines.append(f"- **建议**: {f.suggestion}")
    if f.downgrade_reason:
        lines.append(f"- **分级说明**: {f.downgrade_reason}")
    lines.append(f"- **追溯**: `{f.origin}`（LLM 调用 ID，完整 trace 见 Phase 6）")
    lines.append("")
    return "\n".join(lines)


def render_markdown(
    *,
    task_id: str,
    source: str,
    input_fingerprint: str,
    base_sha: str | None,
    head_sha: str | None,
    model: str,
    prompt_version: str,
    findings: list[Finding],
    work_units: list,
    usage_total: dict,
    tool_results: list[ToolResult],
    unit_notes: list[str],
    dropped_notes: list[str],
    errors: list[str],
    redaction: RedactionReport | None,
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done = sum(1 for u in work_units if _status_value(u) == "done")
    failed = sum(1 for u in work_units if _status_value(u) == "failed")
    high = [f for f in findings if f.confidence == Confidence.high]
    reference = [f for f in findings if f.confidence == Confidence.reference]

    out = ["# Code Review 报告", ""]
    out.append(f"- **任务**: `{task_id}`")
    out.append(f"- **来源**: {source}")
    out.append(f"- **输入指纹**: `{input_fingerprint}`")
    if base_sha or head_sha:
        out.append(f"- **变更区间**: `{base_sha}` → `{head_sha}`")
    out.append(f"- **生成时间**: {generated_at} (UTC)")
    out.append(f"- **模型**: `{model}`（prompt v{prompt_version}）")
    out.append(f"- **工作单元**: {len(work_units)} 个，完成 {done}，失败 {failed}")
    out.append(
        f"- **模型用量**: {usage_total.get('calls', 0)} 次调用，"
        f"输入 {usage_total.get('input_tokens', 0)} tokens，输出 {usage_total.get('output_tokens', 0)} tokens"
        f"（预算闸门将在 Phase 7 启用）"
    )
    if redaction and redaction.matches:
        kinds = ", ".join(f"{k}×{v}" for k, v in redaction.by_kind.items())
        out.append(f"- **脱敏**: 检测并掩码 {redaction.matches} 处疑似 secret（{kinds}）")
    for tr in tool_results:
        parts = []
        if tr.output:
            parts.append(str(tr.output))
        if tr.error:
            parts.append(tr.error)
        detail = "；".join(parts) or "无输出"
        out.append(f"- **工具 `{tr.tool}`**: {tr.status.value} — {detail}")
    out.append("")

    out.append("## 高置信度（可直接采纳）")
    out.append("")
    if high:
        for f in sorted(high, key=lambda x: _SEVERITY_ORDER.index(x.severity)):
            out.append(_finding_section(f))
    else:
        out.append("（无）")
        out.append("")

    out.append("## 仅供参考")
    out.append("")
    if reference:
        for f in sorted(reference, key=lambda x: _SEVERITY_ORDER.index(x.severity)):
            out.append(_finding_section(f))
    else:
        out.append("（无）")
        out.append("")

    if not findings:
        out.append("> 未发现可确认问题。")
        out.append("")

    notes = list(unit_notes) + list(dropped_notes)
    if notes:
        out.append("## 执行说明")
        out.append("")
        for n in notes:
            out.append(f"- {n}")
        out.append("")
    if errors:
        out.append("## 错误")
        out.append("")
        for e in errors:
            out.append(f"- {e}")
        out.append("")
    return "\n".join(out)
