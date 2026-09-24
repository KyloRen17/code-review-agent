from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from code_review_agent.agent.graph import build_review_graph
from code_review_agent.agent.nodes import ReviewPipeline
from code_review_agent.config import load_settings
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.observability import trace as trace_mod
from code_review_agent.persistence.db import create_db_engine, make_session_factory
from code_review_agent.persistence.models import FindingRecord
from code_review_agent.providers.local import LocalDiffProvider
from code_review_agent.publishers.dry_run import DryRunPublisher
from code_review_agent.tools.dispatcher import build_dispatcher


@pytest.fixture
def completed_task(tmp_path, buggy_diff_path, session_factory, tools_config_path):
    from code_review_agent.persistence.models import TaskRecord

    with session_factory() as session:
        session.add(
            TaskRecord(
                id="trace1",
                source="local",
                input_ref=str(buggy_diff_path),
                status="running",
                model="mock-reviewer-v1",
            )
        )
        session.commit()
    settings = load_settings(tools_config_path.parent / "agent.yaml")
    settings.storage.report_dir = str(tmp_path / "runs")
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=MockLLMGateway(),
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=LocalDiffProvider(),
        task_id="trace1",
        session_factory=session_factory,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "trace1", "input_ref": str(buggy_diff_path), "source": "local"},
        {"recursion_limit": 100},
    )
    with session_factory() as session:
        task = session.get(TaskRecord, "trace1")
        task.status = "completed"
        task.fingerprint = final.get("input_fingerprint")
        task.report_path = final.get("report_path")
        session.commit()
    return final, session_factory


def test_trace_chain_links_comment_to_call_unit_task_and_tools(completed_task):
    final, session_factory = completed_task
    with session_factory() as session:
        finding = session.scalars(
            select(FindingRecord)
            .where(FindingRecord.task_id == "trace1", FindingRecord.title == "使用 eval() 执行动态表达式")
        ).one()
        chain = trace_mod.build_trace_chain(session, finding.finding_id)

    assert chain["trace_id"] == "trace1"
    assert chain["finding"]["file"] == "app/auth.py"
    assert chain["finding"]["line"] == 19
    call = chain["llm_call"]
    assert call and call["call_id"] == finding.origin
    assert call["model"] == "mock-reviewer-v1"
    assert call["prompt_version"] == "1.1"
    assert call["input_tokens"] > 0 and call["output_tokens"] > 0
    assert call["finish_reason"] == "stop"
    # prompt 快照包含脱敏后的 diff
    assert "[REDACTED:generic-secret]" in call["user_prompt"]
    assert "sk-live-9f8e7d6c5b4a3210" not in call["user_prompt"]
    assert "<diff>" in call["user_prompt"]
    # 响应快照是可解析的 findings JSON
    parsed = json.loads(call["response"])
    assert any(f["title"] == "使用 eval() 执行动态表达式" for f in parsed["findings"])
    # 工作单元与任务
    assert chain["work_unit"]["unit_id"] == "trace1:app/auth.py#h0"
    assert chain["work_unit"]["status"] == "done"
    assert chain["task"]["source"] == "local"
    assert chain["task"]["input_fingerprint"]
    # 工具结果
    tool_names = {t["tool"] for t in chain["tools"]}
    assert {"diff-stat", "py-ast-check"} <= tool_names


def test_spans_form_node_llm_hierarchy(completed_task):
    _, session_factory = completed_task
    with session_factory() as session:
        spans = trace_mod.load_spans(session, "trace1")
    node_names = {s["name"] for s in spans if s["kind"] == "node"}
    assert {f"node:{n}" for n in [
        "load_input", "security_scan", "normalize_diff", "build_context",
        "select_tools", "execute_tools", "llm_analyze", "validate_findings",
        "generate_report", "publish",
    ]} <= node_names
    assert all(s["status"] == "ok" for s in spans)
    llm_spans = [s for s in spans if s["kind"] == "llm"]
    analysis = [s for s in llm_spans if s["name"].startswith("llm:")]
    rechecks = [s for s in llm_spans if s["name"].startswith("recheck:")]
    assert len(analysis) == 2  # 两个工作单元
    assert len(rechecks) == 3  # 3 条高置信候选复核（agent.yaml: review.recheck=true）
    span_ids = {s["span_id"] for s in spans}
    for s in llm_spans:
        assert s["parent_span_id"] in span_ids
    parent_names = {s["span_id"]: s["name"] for s in spans}
    for s in analysis:
        assert parent_names[s["parent_span_id"]] == "node:llm_analyze"
    for s in rechecks:
        assert parent_names[s["parent_span_id"]] == "node:validate_findings"


def test_export_trace_json_is_redacted_and_parseable(completed_task):
    _, session_factory = completed_task
    with session_factory() as session:
        finding = session.scalars(
            select(FindingRecord).where(FindingRecord.task_id == "trace1")
        ).first()
        chain = trace_mod.build_trace_chain(session, finding.finding_id)
        spans = trace_mod.load_spans(session, "trace1")
    text = trace_mod.export_trace_json(chain, spans)
    assert "sk-live-9f8e7d6c5b4a3210" not in text
    payload = json.loads(text)
    assert payload["trace_id"] == "trace1"
    assert len(payload["spans"]) >= 12


def test_render_trace_text(completed_task):
    _, session_factory = completed_task
    with session_factory() as session:
        finding = session.scalars(
            select(FindingRecord).where(FindingRecord.task_id == "trace1")
        ).first()
        chain = trace_mod.build_trace_chain(session, finding.finding_id)
        spans = trace_mod.load_spans(session, "trace1")
    text = trace_mod.render_trace_text(chain, spans)
    assert "Trace trace1" in text
    assert "LLM 调用" in text
    assert "工作单元" in text
    assert "Spans" in text
    assert finding.title in text


def test_trace_unknown_finding_raises(session_factory):
    with session_factory() as session:
        with pytest.raises(LookupError):
            trace_mod.build_trace_chain(session, "no-such-finding")
