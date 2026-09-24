from __future__ import annotations

import time
import uuid

from ..budget.controller import BudgetController
from ..diff.models import DiffFile
from ..llm.gateway import LLMGateway, LLMRequest
from ..llm.prompts import PROMPT_VERSION, build_recheck_prompt
from ..llm.schemas import parse_recheck_verdict
from ..persistence import ops
from ..security.redactor import redact
from .finding import Confidence, Finding


def _snippet(df: DiffFile, line: int | None, radius: int = 5) -> str:
    numbered = _numbered(df)
    if line is None:
        return "\n".join(f"{n}: {c}" for n, c in numbered[: radius * 2])
    nearby = [f"{n}: {c}" for n, c in numbered if abs(n - line) <= radius]
    return "\n".join(nearby)


def _numbered(df: DiffFile) -> list[tuple[int, str]]:
    out = []
    for hunk in df.hunks:
        for dl in hunk.lines:
            if dl.new_line is not None:
                out.append((dl.new_line, dl.content))
    return out


class RecheckService:
    """高置信候选的预算内复核。

    - 只复核前 `max_rechecks` 条候选（有上限，防失控）；
    - 复核调用同样走预算闸门（不足则跳过复核，不阻塞主流程）；
    - 结果（verdict/reason/call_id）写入 finding.recheck，报告与 trace 可查；
    - rejected → 从输出移除；uncertain → 降级为仅供参考。
    """

    def __init__(
        self,
        gateway: LLMGateway,
        session_factory,
        recorder,
        budget: BudgetController | None = None,
        model_name: str = "",
        max_output_tokens: int = 512,
        max_rechecks: int = 5,
    ) -> None:
        self.gateway = gateway
        self.session_factory = session_factory
        self.recorder = recorder
        self.budget = budget
        self.model_name = model_name
        self.max_output_tokens = max_output_tokens
        self.max_rechecks = max_rechecks

    def verify(
        self,
        task_id: str,
        candidates: list[Finding],
        diff_files: list[DiffFile],
    ) -> tuple[list[Finding], list[str]]:
        results: list[Finding] = []
        notes: list[str] = []
        file_map = {df.path: df for df in diff_files}
        for f in candidates[: self.max_rechecks]:
            call_id = uuid.uuid4().hex[:12]
            reservation = None
            df = file_map.get(f.file)
            snippet = _snippet(df, f.line) if df is not None else ""
            system, prompt = build_recheck_prompt(f, snippet)
            if self.budget is not None:
                reservation = self.budget.try_reserve(
                    task_id, call_id, self.model_name, len(system) + len(prompt), self.max_output_tokens
                )
                if reservation is None:
                    notes.append(f"跳过复核 {f.finding_id}: 预算不足")
                    results.append(f)
                    continue
            request = LLMRequest(
                call_id=call_id,
                model=self.model_name,
                system=system,
                prompt=prompt,
                context={
                    "purpose": "recheck",
                    "file": f.file,
                    "line": f.line,
                    "evidence": f.evidence,
                },
                max_output_tokens=self.max_output_tokens,
            )
            started = time.monotonic()
            try:
                with self.recorder.span(
                    task_id, f"recheck:{f.finding_id}", "llm", {"finding_id": f.finding_id}
                ) as span_id:
                    response = self.gateway.complete(request)
            except Exception as exc:
                if self.budget is not None and reservation is not None:
                    self.budget.release(task_id, reservation)
                notes.append(f"复核失败 {f.finding_id}: {exc}")
                results.append(f)
                continue
            duration_ms = int((time.monotonic() - started) * 1000)
            try:
                verdict = parse_recheck_verdict(response.content)
            except Exception as exc:
                notes.append(f"复核响应无法解析 {f.finding_id}: {exc}")
                verdict = None
            redacted_response, _ = redact(response.content)
            redacted_system, _ = redact(system)
            redacted_user, _ = redact(prompt)
            with self.session_factory() as session:
                ops.save_llm_call(
                    session,
                    call_id=call_id,
                    task_id=task_id,
                    unit_id=f"recheck:{f.finding_id}",
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
            if self.budget is not None and reservation is not None:
                self.budget.settle(
                    task_id, reservation, response.usage.input_tokens, response.usage.output_tokens
                )
            if verdict is None:
                results.append(f)
                continue
            recheck_info = {
                "verdict": verdict.verdict,
                "reason": verdict.reason,
                "call_id": call_id,
            }
            if verdict.verdict == "confirmed":
                results.append(
                    f.model_copy(update={"recheck": recheck_info})
                )
            elif verdict.verdict == "rejected":
                notes.append(f"丢弃 {f.finding_id} ({f.title}): 复核驳回 — {verdict.reason}")
                # 驳回结论持久化（自测发现的缺陷修复）：真实模型的复核裁决是非确定性的，
                # 若不落库，resume 重跑会重新复核甚至“复活”已驳回的 finding
                rejected = f.model_copy(update={"recheck": recheck_info})
                with self.session_factory() as session:
                    ops.save_finding(session, rejected)
            else:
                results.append(
                    f.model_copy(
                        update={
                            "confidence": Confidence.reference,
                            "downgrade_reason": f"复核不确定：{verdict.reason}",
                            "recheck": recheck_info,
                        }
                    )
                )
        return results, notes
