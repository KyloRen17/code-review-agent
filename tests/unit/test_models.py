from __future__ import annotations

import pytest
from pydantic import ValidationError

from code_review_agent.agent.work_units import WorkUnit, WorkUnitStatus
from code_review_agent.llm.schemas import LLMFinding, parse_llm_findings
from code_review_agent.review.finding import Confidence, Finding, Severity
from code_review_agent.tools.base import ToolResult, ToolStatus


def _finding(**overrides) -> dict:
    base = dict(
        finding_id="f1",
        task_id="t1",
        file="a.py",
        line=10,
        title="title",
        description="desc",
    )
    base.update(overrides)
    return base


def test_finding_rejects_unknown_severity():
    with pytest.raises(ValidationError):
        Finding(**_finding(severity="catastrophic"))


def test_finding_rejects_line_below_one():
    with pytest.raises(ValidationError):
        Finding(**_finding(line=0))


def test_finding_defaults_are_conservative():
    f = Finding(**_finding())
    assert f.severity == Severity.info
    assert f.confidence == Confidence.reference


def test_llm_finding_rejects_extra_fields():
    payload = {
        "file": "a.py",
        "title": "t",
        "description": "d",
        "severity": "high",
        "confidence": "reference",
        "surprise": True,
    }
    with pytest.raises(ValidationError):
        LLMFinding.model_validate(payload)


def test_llm_finding_rejects_invalid_confidence():
    payload = {
        "file": "a.py",
        "title": "t",
        "description": "d",
        "severity": "high",
        "confidence": "maybe",
    }
    with pytest.raises(ValidationError):
        LLMFinding.model_validate(payload)


def test_parse_llm_findings_accepts_code_fence():
    text = '```json\n{"findings": []}\n```'
    assert parse_llm_findings(text).findings == []


def test_parse_llm_findings_rejects_garbage():
    with pytest.raises(ValidationError):
        parse_llm_findings("我觉得这段代码还行")


def test_work_unit_defaults_to_pending():
    u = WorkUnit(
        unit_id="t:a.py#h0", file="a.py", hunk_index=0, start_line=1, end_line=5, content="@@"
    )
    assert u.status == WorkUnitStatus.pending


def test_tool_result_defaults():
    tr = ToolResult(tool="x")
    assert tr.status == ToolStatus.success
    assert tr.output == {}
    assert tr.duration_ms == 0
