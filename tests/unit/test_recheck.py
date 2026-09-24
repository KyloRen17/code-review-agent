from __future__ import annotations

import json

import pytest

from code_review_agent.agent.work_units import WorkUnitStatus
from code_review_agent.config import load_settings
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.observability.spans import SpanRecorder
from code_review_agent.review.finding import Confidence, Finding, Severity
from code_review_agent.review.recheck import RecheckService
from code_review_agent.llm.gateway import LLMRequest, LLMResponse, Usage
from code_review_agent.diff import DiffFile, DiffHunk, DiffLine
from code_review_agent.llm.schemas import parse_recheck_verdict


def _finding(**overrides) -> Finding:
    base = dict(
        finding_id="f1",
        task_id="t1",
        file="app.py",
        line=2,
        title="问题",
        description="desc",
        evidence="x = eval(user)",
        severity=Severity.high,
        confidence=Confidence.high,
    )
    base.update(overrides)
    return Finding(**base)


def _diff_files() -> list[DiffFile]:
    hunk = DiffHunk(
        header="@@ -1,2 +1,3 @@",
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=3,
        lines=[
            DiffLine(kind="context", old_line=1, new_line=1, content="ctx"),
            DiffLine(kind="added", new_line=2, content="x = eval(user)"),
            DiffLine(kind="context", old_line=2, new_line=3, content="ctx2"),
        ],
    )
    return [DiffFile(old_path="app.py", new_path="app.py", hunks=[hunk])]


class _FakeGateway:
    name = "fake"
    verdicts: dict[str, str] = {}

    def complete(self, request: LLMRequest) -> LLMResponse:
        fid = str(request.context.get("file", ""))
        payload = self.verdicts.get(
            "default", json.dumps({"verdict": "uncertain", "reason": "信息不足"})
        )
        return LLMResponse(
            call_id=request.call_id,
            model="fake",
            content=payload,
            usage=Usage(input_tokens=10, output_tokens=5),
        )


def _service(session_factory, gateway=None) -> RecheckService:
    return RecheckService(
        gateway or MockLLMGateway(),
        session_factory,
        SpanRecorder(session_factory),
        budget=None,
        model_name="mock-reviewer-v1",
    )


def test_confirmed_finding_keeps_high_with_recheck_info(session_factory):
    service = _service(session_factory)
    findings, notes = service.verify("t1", [_finding()], _diff_files())
    assert findings[0].confidence == Confidence.high
    assert findings[0].recheck["verdict"] == "confirmed"
    assert findings[0].recheck["call_id"]
    assert notes == []


def test_uncertain_finding_is_downgraded(session_factory):
    gateway = _FakeGateway()
    gateway.verdicts = {"default": json.dumps({"verdict": "uncertain", "reason": "看不清"})}
    service = _service(session_factory, gateway)
    findings, _ = service.verify("t1", [_finding(evidence="nothing here")], _diff_files())
    assert findings[0].confidence == Confidence.reference
    assert "复核不确定：看不清" in findings[0].downgrade_reason
    assert findings[0].recheck["verdict"] == "uncertain"


def test_rejected_finding_is_removed_with_note(session_factory):
    gateway = _FakeGateway()
    gateway.verdicts = {"default": json.dumps({"verdict": "rejected", "reason": "误报"})}
    service = _service(session_factory, gateway)
    findings, notes = service.verify("t1", [_finding()], _diff_files())
    assert findings == []
    assert any("复核驳回" in n for n in notes)


def test_recheck_max_caps_candidates(session_factory):
    gateway = _FakeGateway()
    original_complete = gateway.complete
    calls = {"n": 0}

    def counting_complete(request):
        calls["n"] += 1
        return original_complete(request)

    gateway.complete = counting_complete
    service = RecheckService(
        gateway,
        session_factory,
        SpanRecorder(session_factory),
        model_name="m",
        max_output_tokens=64,
        max_rechecks=2,
    )
    candidates = [_finding(finding_id=f"f{i}") for i in range(5)]
    findings, _ = service.verify("t1", candidates, _diff_files())
    assert calls["n"] == 2  # 上限生效，其余候选不再调用
    assert all(f.recheck for f in findings)


def test_recheck_parse_failure_keeps_finding_and_notes(session_factory):
    gateway = _FakeGateway()
    gateway.verdicts = {"default": "这不是 JSON"}
    service = _service(session_factory, gateway)
    findings, notes = service.verify("t1", [_finding()], _diff_files())
    assert len(findings) == 1
    assert findings[0].confidence == Confidence.high  # 解析失败不影响既有分级
    assert any("无法解析" in n for n in notes)


def test_parse_recheck_verdict():
    v = parse_recheck_verdict('{"verdict": "confirmed", "reason": "ok"}')
    assert v.verdict == "confirmed"
    with pytest.raises(Exception):
        parse_recheck_verdict('{"verdict": "maybe"}')


def test_full_pipeline_recheck_visible_in_report_and_trace(
    tmp_path, buggy_diff_path, session_factory, tools_config_path
):
    from code_review_agent.agent.graph import build_review_graph
    from code_review_agent.agent.nodes import ReviewPipeline
    from code_review_agent.persistence.models import FindingRecord
    from code_review_agent.providers.local import LocalDiffProvider
    from code_review_agent.publishers.dry_run import DryRunPublisher
    from code_review_agent.tools.dispatcher import build_dispatcher
    from sqlalchemy import select

    settings = load_settings(tools_config_path.parent / "agent.yaml")
    settings.storage.report_dir = str(tmp_path / "runs")
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=MockLLMGateway(),
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=LocalDiffProvider(),
        task_id="recheck1",
        session_factory=session_factory,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "recheck1", "input_ref": str(buggy_diff_path), "source": "local"},
        {"recursion_limit": 100},
    )
    high = [f for f in final["validated_findings"] if f.confidence == Confidence.high]
    assert len(high) == 3
    assert all(f.recheck and f.recheck["verdict"] == "confirmed" for f in high)

    from pathlib import Path

    report = Path(final["report_path"]).read_text(encoding="utf-8")
    assert "**复核**" in report and "confirmed" in report

    with session_factory() as session:
        record = session.scalars(
            select(FindingRecord)
            .where(FindingRecord.task_id == "recheck1", FindingRecord.confidence == "high")
            .limit(1)
        ).first()
        assert record.recheck
        assert json.loads(record.recheck)["verdict"] == "confirmed"
