from __future__ import annotations

import logging
import uuid
from pathlib import Path

from ..config import AgentSettings
from ..diff.parser import parse_unified_diff
from ..llm.gateway import LLMGateway, LLMRequest
from ..llm.prompts import PROMPT_VERSION, build_review_prompt
from ..llm.schemas import parse_llm_findings
from ..providers.base import RepositoryProvider
from ..publishers.base import ReviewPublisher
from ..review.finding import Confidence, Finding, Severity
from ..review.report import render_markdown
from ..review.validator import validate_and_grade
from ..security.redactor import redact
from ..tools.base import ToolResult, ToolStatus
from ..tools.registry import ToolRegistry
from .state import GraphState
from .work_units import WorkUnit, WorkUnitStatus, build_work_units

logger = logging.getLogger("cra.pipeline")


class ReviewPipeline:
    """所有 LangGraph 节点的宿主。

    节点只做确定性的编排：安全扫描、预算与验证决策均由本类（而非 LLM）执行。
    """

    def __init__(
        self,
        *,
        settings: AgentSettings,
        gateway: LLMGateway,
        registry: ToolRegistry,
        publisher: ReviewPublisher,
        provider: RepositoryProvider,
        task_id: str,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.registry = registry
        self.publisher = publisher
        self.provider = provider
        self.task_id = task_id

    # ------------------------------------------------------------------
    def load_input(self, state: GraphState) -> dict:
        review_input = self.provider.fetch(state["input_ref"])
        logger.info("input_loaded", extra={"source": review_input.source, "bytes": len(review_input.raw_diff)})
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
            logger.warning("secrets_redacted", extra={"matches": report.matches, "kinds": report.by_kind})
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
        logger.info("work_units_built", extra={"units": len(units), "skipped": len(notes)})
        return {"work_units": units, "unit_notes": notes}

    def select_tools(self, state: GraphState) -> dict:
        return {"tool_selection": sorted(self.registry.names())}

    def execute_tools(self, state: GraphState) -> dict:
        results: list[ToolResult] = []
        for name in state.get("tool_selection", []):
            tool = self.registry.get(name)
            if tool is None:
                results.append(
                    ToolResult(tool=name, work_unit_id="*", status=ToolStatus.failure, error="工具未注册")
                )
                continue
            try:
                results.append(
                    tool.run({"diff_files": state["diff_files"], "work_units": state["work_units"]})
                )
            except Exception as exc:
                results.append(
                    ToolResult(tool=name, work_unit_id="*", status=ToolStatus.failure, error=str(exc))
                )
        return {"tool_results": results}

    def llm_analyze(self, state: GraphState) -> dict:
        findings: list[Finding] = []
        errors: list[str] = []
        updated: list[WorkUnit] = []
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        for unit in state.get("work_units", []):
            if unit.status != WorkUnitStatus.pending:
                updated.append(unit)
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
            try:
                response = self.gateway.complete(request)
            except Exception as exc:
                errors.append(f"LLM 调用失败 ({unit.unit_id}): {exc}")
                updated.append(unit.model_copy(update={"status": WorkUnitStatus.failed}))
                continue
            try:
                parsed = parse_llm_findings(response.content)
            except Exception as exc:
                errors.append(f"LLM 输出未通过 Schema 校验 ({unit.unit_id}): {exc}")
                updated.append(unit.model_copy(update={"status": WorkUnitStatus.failed}))
                continue
            for lf in parsed.findings:
                findings.append(
                    Finding(
                        task_id=state["task_id"],
                        finding_id=uuid.uuid4().hex[:12],
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
            updated.append(unit.model_copy(update={"status": WorkUnitStatus.done}))
            usage["calls"] += 1
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
        return {"findings": findings, "work_units": updated, "usage_total": usage, "errors": errors}

    def validate_findings(self, state: GraphState) -> dict:
        validated, dropped = validate_and_grade(
            state["task_id"], state.get("findings", []), state.get("diff_files", [])
        )
        return {"validated_findings": validated, "dropped_notes": dropped}

    def generate_report(self, state: GraphState) -> dict:
        findings = state.get("validated_findings", [])
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
            usage_total=state.get("usage_total", {}),
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
        receipt = self.publisher.publish(
            task_id=state["task_id"], report_path=state["report_path"], findings=state.get("validated_findings", [])
        )
        return {"publish_receipt": receipt.model_dump()}
