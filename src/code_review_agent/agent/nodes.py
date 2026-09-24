from __future__ import annotations

import hashlib
import logging
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import AgentSettings
from ..diff.parser import parse_unified_diff
from ..llm.gateway import LLMGateway, LLMRequest
from ..llm.prompts import PROMPT_VERSION, build_review_prompt
from ..llm.schemas import parse_llm_findings
from ..observability.spans import SpanRecorder, current_span_id
from ..persistence import ops
from ..persistence.models import LLMCallRecord
from ..providers.base import RepositoryProvider
from ..publishers.base import PublishReceipt, ReviewPublisher
from ..review.finding import Confidence, Finding, Severity
from ..review.report import render_markdown
from ..review.validator import validate_and_grade
from ..security.redactor import redact
from ..tools.base import ToolStatus
from ..tools.dispatcher import ToolDispatcher
from .state import GraphState
from .work_units import WorkUnit, WorkUnitStatus, build_work_units

logger = logging.getLogger("cra.pipeline")


def _stable_finding_id(task_id: str, file: str, line: int | None, title: str) -> str:
    raw = f"{task_id}|{file}|{line}|{title}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


class ReviewPipeline:
    """所有 LangGraph 节点的宿主。

    节点只做确定性的编排：安全扫描、预算与验证决策均由本类（而非 LLM）执行。
    llm_analyze 按工作单元增量持久化（unit 状态 + findings + LLM 调用账目），
    崩溃后 resume 时已完成单元直接跳过、不重复调用模型。
    """

    def __init__(
        self,
        *,
        settings: AgentSettings,
        gateway: LLMGateway,
        dispatcher: ToolDispatcher,
        publisher: ReviewPublisher,
        provider: RepositoryProvider,
        task_id: str,
        session_factory: sessionmaker[Session],
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.publisher = publisher
        self.provider = provider
        self.task_id = task_id
        self.session_factory = session_factory
        self.recorder = SpanRecorder(session_factory)

    # ------------------------------------------------------------------
    def load_input(self, state: GraphState) -> dict:
        review_input = self.provider.fetch(state["input_ref"])
        logger.info(
            "input_loaded",
            extra={"source": review_input.source, "bytes": len(review_input.raw_diff)},
        )
        return {
            "raw_diff": review_input.raw_diff,
            "input_fingerprint": review_input.fingerprint,
            "source": review_input.source,
            "base_sha": review_input.base_sha,
            "head_sha": review_input.head_sha,
        }

    def security_scan(self, state: GraphState) -> dict:
        redacted, report = redact(state["raw_diff"])
        if report.matches:
            logger.warning(
                "secrets_redacted",
                extra={"matches": report.matches, "kinds": report.by_kind},
            )
        return {"redacted_diff": redacted, "redaction": report}

    def normalize_diff(self, state: GraphState) -> dict:
        diff_files = parse_unified_diff(state["redacted_diff"])
        if not diff_files:
            raise ValueError("diff 中未解析到任何文件变更")
        return {"diff_files": diff_files}

    def build_context(self, state: GraphState) -> dict:
        units, notes = build_work_units(
            state["task_id"], state["diff_files"], self.settings.limits.max_work_unit_bytes
        )
        logger.info(
            "work_units_built", extra={"units": len(units), "skipped": len(notes)}
        )
        return {"work_units": units, "unit_notes": notes}

    def select_tools(self, state: GraphState) -> dict:
        return {"tool_selection": self.dispatcher.enabled_names()}

    def execute_tools(self, state: GraphState) -> dict:
        results = self.dispatcher.run_all(
            task_id=state["task_id"],
            diff_files=state.get("diff_files", []),
            work_units=state.get("work_units", []),
        )
        span_id = current_span_id()
        with self.session_factory() as session:
            for r in results:
                ops.save_tool_result(
                    session,
                    task_id=state["task_id"],
                    tool=r.tool,
                    work_unit_id=r.work_unit_id,
                    status=r.status.value,
                    output=r.output,
                    error=r.error,
                    duration_ms=r.duration_ms,
                    attempts=r.attempts,
                    span_id=span_id,
                )
        failures = [r for r in results if r.status == ToolStatus.failure]
        if failures:
            logger.warning(
                "tool_failures",
                extra={"count": len(failures), "tools": [f.tool for f in failures]},
            )
        return {"tool_results": results}

    def llm_analyze(self, state: GraphState) -> dict:
        task_id = state["task_id"]
        findings: list[Finding] = []
        errors: list[str] = []
        updated: list[WorkUnit] = []
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

        with self.session_factory() as session:
            persisted = ops.load_work_units(session, task_id)
            done_unit_ids = {
                uid for uid, record in persisted.items() if record.status == "done"
            }
            failed_count = sum(1 for r in persisted.values() if r.status == "failed")
            if done_unit_ids:
                call_unit = dict(
                    session.execute(
                        select(LLMCallRecord.call_id, LLMCallRecord.unit_id).where(
                            LLMCallRecord.task_id == task_id
                        )
                    ).all()
                )
                done_calls = {
                    cid for cid, uid in call_unit.items() if uid in done_unit_ids
                }
                findings.extend(f for f in ops.load_findings(session, task_id) if f.origin in done_calls)

        for unit in state.get("work_units", []):
            if unit.unit_id in done_unit_ids:
                updated.append(unit.model_copy(update={"status": WorkUnitStatus.done}))
                continue
            call_id = uuid.uuid4().hex[:12]
            system, prompt = build_review_prompt(unit)
            request = LLMRequest(
                call_id=call_id,
                model=self.settings.model.name,
                system=system,
                prompt=prompt,
                context={"file": unit.file, "code": unit.content, "new_start": unit.start_line},
                max_output_tokens=self.settings.model.max_output_tokens,
            )
            started = time.monotonic()
            try:
                with self.recorder.span(
                    task_id,
                    f"llm:{call_id}",
                    "llm",
                    {"unit_id": unit.unit_id, "model": self.settings.model.name},
                ) as span_id:
                    response = self.gateway.complete(request)
            except Exception as exc:
                errors.append(f"LLM 调用失败 ({unit.unit_id}): {exc}")
                self._persist_unit(
                    task_id, unit, WorkUnitStatus.failed, error=str(exc)
                )
                updated.append(unit.model_copy(update={"status": WorkUnitStatus.failed}))
                continue
            try:
                parsed = parse_llm_findings(response.content)
            except Exception as exc:
                errors.append(f"LLM 输出未通过 Schema 校验 ({unit.unit_id}): {exc}")
                self._persist_unit(
                    task_id, unit, WorkUnitStatus.failed, error=f"schema: {exc}"
                )
                updated.append(unit.model_copy(update={"status": WorkUnitStatus.failed}))
                continue
            unit_findings: list[Finding] = []
            for lf in parsed.findings:
                unit_findings.append(
                    Finding(
                        finding_id=_stable_finding_id(task_id, lf.file, lf.line, lf.title),
                        task_id=task_id,
                        file=lf.file,
                        line=lf.line,
                        title=lf.title,
                        description=lf.description,
                        trigger=lf.trigger,
                        evidence=lf.evidence,
                        suggestion=lf.suggestion,
                        severity=Severity(lf.severity),
                        confidence=Confidence(lf.confidence),
                        origin=call_id,
                    )
                )
            findings.extend(unit_findings)
            duration_ms = int((time.monotonic() - started) * 1000)
            redacted_system, _ = redact(request.system)
            redacted_user, _ = redact(request.prompt)
            redacted_response, _ = redact(response.content)
            with self.session_factory() as session:
                ops.save_llm_call(
                    session,
                    call_id=call_id,
                    task_id=task_id,
                    unit_id=unit.unit_id,
                    model=response.model,
                    prompt_version=PROMPT_VERSION,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    duration_ms=duration_ms,
                    finish_reason=response.finish,
                    span_id=span_id,
                    system_prompt=redacted_system,
                    user_prompt=redacted_user,
                    response=redacted_response,
                )
                for f in unit_findings:
                    ops.save_finding(session, f)
            self._persist_unit(task_id, unit, WorkUnitStatus.done)
            updated.append(unit.model_copy(update={"status": WorkUnitStatus.done}))
            usage["calls"] += 1
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens

        if failed_count:
            logger.info("units_retrying", extra={"previously_failed": failed_count})
        return {
            "findings": findings,
            "work_units": updated,
            "usage_total": usage,
            "errors": errors,
        }

    def _persist_unit(
        self, task_id: str, unit: WorkUnit, status: WorkUnitStatus, error: str | None = None
    ) -> None:
        with self.session_factory() as session:
            ops.save_work_unit(
                session,
                task_id=task_id,
                unit_id=unit.unit_id,
                file=unit.file,
                status=status.value,
                fingerprint=unit.fingerprint,
                error=error,
            )

    def validate_findings(self, state: GraphState) -> dict:
        validated, dropped = validate_and_grade(
            state["task_id"], state.get("findings", []), state.get("diff_files", [])
        )
        return {"validated_findings": validated, "dropped_notes": dropped}

    def generate_report(self, state: GraphState) -> dict:
        findings = state.get("validated_findings", [])
        with self.session_factory() as session:
            usage_total = ops.load_usage(session, state["task_id"])
        report_dir = Path(self.settings.storage.report_dir) / state["task_id"]
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "report.md"
        content = render_markdown(
            task_id=state["task_id"],
            source=state.get("source", "local"),
            input_fingerprint=state.get("input_fingerprint", ""),
            base_sha=state.get("base_sha"),
            head_sha=state.get("head_sha"),
            model=self.settings.model.name,
            prompt_version=PROMPT_VERSION,
            findings=findings,
            work_units=state.get("work_units", []),
            usage_total=usage_total,
            tool_results=state.get("tool_results", []),
            unit_notes=state.get("unit_notes", []),
            dropped_notes=state.get("dropped_notes", []),
            errors=state.get("errors", []),
            redaction=state.get("redaction"),
        )
        report_path.write_text(content, encoding="utf-8")
        logger.info("report_written", extra={"path": str(report_path)})
        return {"report_path": str(report_path)}

    def publish(self, state: GraphState) -> dict:
        task_id = state["task_id"]
        mode = self.publisher.mode
        with self.session_factory() as session:
            record = ops.get_publication(session, task_id, mode)
            if record is not None and record.status in ("sent", "confirmed", "dry_run"):
                receipt = PublishReceipt.model_validate_json(record.receipt or "{}")
                return {"publish_receipt": receipt.model_dump()}
        receipt = self.publisher.publish(
            task_id=task_id,
            report_path=state["report_path"],
            findings=state.get("validated_findings", []),
        )
        with self.session_factory() as session:
            ops.save_publication(
                session,
                task_id=task_id,
                mode=mode,
                idempotency_key=f"{task_id}:{mode}",
                status="sent" if receipt.published else "dry_run",
                detail=receipt.detail,
                receipt=receipt.model_dump_json(),
            )
        return {"publish_receipt": receipt.model_dump()}
