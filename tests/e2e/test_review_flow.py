from __future__ import annotations

from typer.testing import CliRunner

from code_review_agent.cli import app
from code_review_agent.persistence.db import create_db_engine, make_session_factory
from code_review_agent.persistence.models import FindingRecord, TaskRecord

runner = CliRunner()


def _run(input_ref, tmp_path, extra=()):
    db = tmp_path / "db.sqlite"
    report_dir = tmp_path / "runs"
    args = [
        "review",
        str(input_ref),
        "--config",
        str(_configs() / "agent.yaml"),
        "--tools",
        str(_configs() / "tools.yaml"),
        "--db",
        str(db),
        "--report-dir",
        str(report_dir),
        *extra,
    ]
    result = runner.invoke(app, args)
    return result, db, report_dir


def _configs():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "configs"


def test_cli_review_on_buggy_diff_end_to_end(tmp_path, buggy_diff_path):
    result, db, report_dir = _run(buggy_diff_path, tmp_path)
    assert result.exit_code == 0, result.output
    assert "任务" in result.output
    assert "高置信 3" in result.output

    engine = create_db_engine(db)
    with make_session_factory(engine)() as session:
        task = session.query(TaskRecord).one()
        assert task.status == "completed"
        assert task.report_path and task.report_path.endswith("report.md")
        rows = session.query(FindingRecord).filter(FindingRecord.task_id == task.id).all()
        assert len(rows) == 7
        assert sum(1 for r in rows if r.confidence == "high") == 3

    report_text = report_dir.joinpath(task.id, "report.md").read_text(encoding="utf-8")
    assert "高置信度（可直接采纳）" in report_text
    assert "使用 eval() 执行动态表达式" in report_text
    assert report_text.count("\n### [") == 7  # 报告中的 finding 数与 DB 一致
    assert "**复核**" in report_text and "confirmed" in report_text
    expected_titles = {
        "subprocess 使用 shell=True",
        "使用 eval() 执行动态表达式",
        "疑似硬编码凭证",
        "异常被静默吞掉",
        "可变默认参数",
    }
    actual_titles = {r.title for r in rows}
    assert expected_titles <= actual_titles
    assert "触发条件" in report_text
    assert "sk-live-9f8e7d6c5b4a3210" not in report_text
    assert "[REDACTED:generic-secret]" in report_text
    assert "**工具 `diff-stat`**: success" in report_text


def test_cli_review_on_clean_diff_reports_no_findings(tmp_path, clean_diff_path):
    result, db, report_dir = _run(clean_diff_path, tmp_path)
    assert result.exit_code == 0, result.output
    engine = create_db_engine(db)
    with make_session_factory(engine)() as session:
        task = session.query(TaskRecord).one()
        assert task.status == "completed"
        assert session.query(FindingRecord).count() == 0
    report_text = report_dir.joinpath(task.id, "report.md").read_text(encoding="utf-8")
    assert "未发现可确认问题" in report_text


def test_cli_review_failure_on_missing_input(tmp_path):
    result, db, _ = _run(tmp_path / "no-such.diff", tmp_path)
    assert result.exit_code == 1
    engine = create_db_engine(db)
    with make_session_factory(engine)() as session:
        task = session.query(TaskRecord).one()
        assert task.status == "failed"
        assert "diff 文件不存在" in (task.error or "")


def test_cli_review_on_syntax_error_diff(tmp_path, syntax_diff_path):
    result, db, report_dir = _run(syntax_diff_path, tmp_path)
    assert result.exit_code == 0, result.output
    engine = create_db_engine(db)
    with make_session_factory(engine)() as session:
        task = session.query(TaskRecord).one()
        assert task.status == "completed"
    report_text = report_dir.joinpath(task.id, "report.md").read_text(encoding="utf-8")
    assert "py-ast-check" in report_text
    assert "语法错误" in report_text


def test_cli_trace_command_returns_full_chain(tmp_path, buggy_diff_path):
    result, db, report_dir = _run(buggy_diff_path, tmp_path)
    assert result.exit_code == 0, result.output
    reports = list(report_dir.glob("*/report.md"))
    assert reports
    report_text = reports[0].read_text(encoding="utf-8")
    import re

    m = re.search(r"追溯.*`([0-9a-f]{12})`", report_text)
    assert m, "报告中应包含 finding 追溯 ID"
    finding_id = m.group(1)

    trace_result = runner.invoke(
        app,
        [
            "trace",
            finding_id,
            "--db",
            str(db),
        ],
    )
    assert trace_result.exit_code == 0, trace_result.output
    assert "LLM 调用" in trace_result.output
    assert "[REDACTED:generic-secret]" in trace_result.output
    assert "sk-live-9f8e7d6c5b4a3210" not in trace_result.output
    assert "Spans" in trace_result.output

    export_path = tmp_path / "trace.json"
    export_result = runner.invoke(
        app, ["trace", finding_id, "--db", str(db), "--export", str(export_path)]
    )
    assert export_result.exit_code == 0, export_result.output
    import json as _json

    payload = _json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["finding"]["finding_id"] == finding_id
    assert len(payload["spans"]) >= 12
    assert "sk-live-9f8e7d6c5b4a3210" not in export_path.read_text(encoding="utf-8")


def test_cli_publish_flag_gates_and_records(tmp_path, buggy_diff_path, monkeypatch):
    import code_review_agent.cli as cli_mod
    from code_review_agent.publishers.base import PublishReceipt

    calls = {"n": 0}
    original_builder = cli_mod.build_review_publisher

    class _RecordingPublisher:
        mode = "github"

        def publish(self, **kwargs):
            calls["n"] += 1
            return PublishReceipt(
                mode="github",
                published=True,
                posted_comments=3,
                detail=f"发布到 PR：新增行级评论 3 条（第 {calls['n']} 次调用）",
            )

    def fake_builder(source, publish):
        if publish:
            return _RecordingPublisher()
        return original_builder(source, publish)

    monkeypatch.setattr(cli_mod, "build_review_publisher", fake_builder)

    # 默认不带 --publish → dry-run，不调用远程发布器
    result, db, _ = _run(buggy_diff_path, tmp_path)
    assert result.exit_code == 0, result.output
    assert "dry_run" in result.output

    # --publish 显式授权 → 走远程发布器并记录
    result2, db, _ = _run(buggy_diff_path, tmp_path, extra=["--publish"])
    assert result2.exit_code == 0, result2.output
    assert "发布到 PR" in result2.output
    engine = create_db_engine(db)
    with make_session_factory(engine)() as session:
        from code_review_agent.persistence.models import PublicationRecord
        from sqlalchemy import select

        records = session.scalars(
            select(PublicationRecord).where(PublicationRecord.mode == "github")
        ).all()
        assert len(records) == 1
        assert records[0].status == "sent"
        task_id = records[0].task_id

    # resume（同任务）→ 已有 sent 记录 → 幂等跳过，不再调用发布器
    count_before = calls["n"]
    result3 = runner.invoke(
        app,
        [
            "resume",
            task_id,
            "--config",
            str(_configs() / "agent.yaml"),
            "--tools",
            str(_configs() / "tools.yaml"),
            "--db",
            str(db),
            "--report-dir",
            str(tmp_path / "runs"),
            "--publish",
        ],
    )
    assert result3.exit_code == 0, result3.output
    assert calls["n"] == count_before


def test_cli_publish_with_local_diff_rejected(tmp_path, buggy_diff_path):
    result, db, _ = _run(buggy_diff_path, tmp_path, extra=["--publish"])
    assert result.exit_code == 1
    assert "不支持" in result.output


def test_cli_provider_error_has_clear_status(tmp_path):
    result, db, _ = _run("https://github.com/acme/widgets/pull/not-a-number", tmp_path)
    assert result.exit_code == 1
    assert "invalid_input" in result.output
    engine = create_db_engine(db)
    with make_session_factory(engine)() as session:
        task = session.query(TaskRecord).one()
        assert task.status == "failed"
        assert task.source == "github"
        assert "invalid_input" in (task.error or "")
