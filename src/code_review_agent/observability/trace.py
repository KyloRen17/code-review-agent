from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence import ops
from ..persistence.models import FindingRecord, LLMCallRecord, TaskRecord, WorkUnitRecord
from ..security.redactor import redact


def build_trace_chain(session: Session, finding_id: str, task_id: str | None = None) -> dict:
    """构建 Comment → Finding → LLM Call / Work Unit → Task → Spans / Tools 的追溯链。

    所有文本快照在入库前已脱敏；导出时再统一过一遍 redact 作为纵深防御。
    """
    stmt = select(FindingRecord).where(FindingRecord.finding_id == finding_id)
    if task_id:
        stmt = stmt.where(FindingRecord.task_id == task_id)
    finding = session.scalars(stmt).first()
    if finding is None:
        raise LookupError(f"未找到 finding: {finding_id}")
    task_id = finding.task_id

    task = session.get(TaskRecord, task_id)
    unit = None
    if finding.origin:
        unit_id = session.scalar(
            select(LLMCallRecord.unit_id).where(LLMCallRecord.call_id == finding.origin)
        )
        if unit_id:
            unit = session.scalar(
                select(WorkUnitRecord).where(
                    WorkUnitRecord.task_id == task_id, WorkUnitRecord.unit_id == unit_id
                )
            )

    llm_call = ops.load_llm_call(session, finding.origin) if finding.origin else None

    return {
        "trace_id": task_id,
        "finding": {
            "finding_id": finding.finding_id,
            "file": finding.file,
            "line": finding.line,
            "title": finding.title,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "description": finding.description,
            "trigger": finding.trigger,
            "evidence": finding.evidence,
            "suggestion": finding.suggestion,
            "origin_call_id": finding.origin,
        },
        "llm_call": (
            {
                "call_id": llm_call.call_id,
                "model": llm_call.model,
                "prompt_version": llm_call.prompt_version,
                "unit_id": llm_call.unit_id,
                "input_tokens": llm_call.input_tokens,
                "output_tokens": llm_call.output_tokens,
                "duration_ms": llm_call.duration_ms,
                "finish_reason": llm_call.finish_reason,
                "span_id": llm_call.span_id,
                "system_prompt": llm_call.system_prompt,
                "user_prompt": llm_call.user_prompt,
                "response": llm_call.response,
            }
            if llm_call
            else None
        ),
        "work_unit": (
            {
                "unit_id": unit.unit_id,
                "file": unit.file,
                "status": unit.status,
                "fingerprint": unit.fingerprint,
                "error": unit.error,
            }
            if unit
            else None
        ),
        "task": (
            {
                "task_id": task.id,
                "source": task.source,
                "input_ref": task.input_ref,
                "input_fingerprint": task.fingerprint,
                "base_sha": task.base_sha,
                "head_sha": task.head_sha,
                "status": task.status,
                "model": task.model,
                "report_path": task.report_path,
            }
            if task
            else None
        ),
        "tools": [
            {
                "tool": t.tool,
                "work_unit_id": t.work_unit_id,
                "status": t.status,
                "output": json.loads(t.output) if t.output else None,
                "error": t.error,
                "duration_ms": t.duration_ms,
                "attempts": t.attempts,
                "span_id": t.span_id,
            }
            for t in ops.load_tool_results(session, task_id)
        ],
    }


def find_task_ids_for_finding(session: Session, finding_id: str) -> list[str]:
    rows = session.scalars(
        select(FindingRecord.task_id).where(FindingRecord.finding_id == finding_id)
    ).all()
    return sorted(set(rows))


def load_spans(session: Session, task_id: str) -> list[dict]:
    from ..persistence.models import SpanRecord

    records = session.scalars(
        select(SpanRecord).where(SpanRecord.trace_id == task_id).order_by(SpanRecord.started_at)
    ).all()
    return [
        {
            "span_id": r.span_id,
            "parent_span_id": r.parent_span_id,
            "name": r.name,
            "kind": r.kind,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": r.duration_ms,
            "attributes": json.loads(r.attributes) if r.attributes else None,
            "error": r.error_message,
        }
        for r in records
    ]


def export_trace_json(chain: dict, spans: list[dict]) -> str:
    payload = json.dumps({**chain, "spans": spans}, ensure_ascii=False, indent=2, default=str)
    redacted, _ = redact(payload)  # 快照入库前已脱敏；导出再过一遍作为纵深防御
    return redacted


def render_trace_text(chain: dict, spans: list[dict]) -> str:
    finding = chain["finding"]
    call = chain.get("llm_call") or {}
    task = chain.get("task") or {}
    unit = chain.get("work_unit") or {}
    lines = [
        f"# Trace {chain['trace_id']} / finding {finding['finding_id']}",
        "",
        "## Finding（评论）",
        f"- 标题: {finding['title']}",
        f"- 位置: {finding['file']}:{finding['line']}",
        f"- 严重性: {finding['severity']}  置信度: {finding['confidence']}",
        f"- 说明: {finding['description']}",
    ]
    if finding.get("trigger"):
        lines.append(f"- 触发条件: {finding['trigger']}")
    if finding.get("evidence"):
        lines.append(f"- 证据: {finding['evidence']}")
    lines += [
        "",
        "## LLM 调用（生成该 finding 的调用）",
        f"- call_id: {call.get('call_id')}  span: {call.get('span_id')}",
        f"- 模型: {call.get('model')}  prompt v{call.get('prompt_version')}  finish: {call.get('finish_reason')}",
        f"- 用量: 输入 {call.get('input_tokens')} / 输出 {call.get('output_tokens')} tokens，耗时 {call.get('duration_ms')}ms",
    ]
    if call.get("user_prompt"):
        preview = str(call["user_prompt"]).strip()[:400]
        lines += ["- user prompt（脱敏快照，截断）:", "  ```", f"  {preview}", "  ```"]
    if call.get("response"):
        preview = str(call["response"]).strip()[:400]
        lines += ["- 模型响应（脱敏快照，截断）:", "  ```", f"  {preview}", "  ```"]
    if unit:
        lines += [
            "",
            "## 工作单元",
            f"- {unit.get('unit_id')}  状态: {unit.get('status')}  指纹: {unit.get('fingerprint')}",
        ]
    lines += [
        "",
        "## 任务",
        f"- {task.get('task_id')}  来源: {task.get('source')}  状态: {task.get('status')}",
        f"- 输入指纹: {task.get('input_fingerprint')}",
    ]
    if task.get("base_sha") or task.get("head_sha"):
        lines.append(f"- 变更区间: {task.get('base_sha')} → {task.get('head_sha')}")
    tools = chain.get("tools") or []
    if tools:
        lines += ["", "## 工具结果"]
        for t in tools:
            lines.append(
                f"- {t['tool']} [{t['status']}] unit={t['work_unit_id']} {t['duration_ms']}ms ×{t['attempts']}"
                + (f" — {t['error']}" if t.get("error") else "")
            )
    lines += ["", f"## Spans（{len(spans)} 个）"]
    for s in spans:
        indent = "  " if s["parent_span_id"] else ""
        lines.append(
            f"{indent}- {s['name']} [{s['kind']}/{s['status']}] {s['duration_ms']}ms"
            + (f" — {s['error']}" if s.get("error") else "")
        )
    return "\n".join(lines)
