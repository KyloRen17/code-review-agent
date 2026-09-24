from __future__ import annotations

import pytest
from sqlalchemy import func, select

from code_review_agent.agent.graph import build_review_graph
from code_review_agent.agent.nodes import ReviewPipeline
from code_review_agent.agent.work_units import WorkUnitStatus
from code_review_agent.checkpoint import create_checkpointer
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.persistence.db import create_db_engine, init_db, make_session_factory
from code_review_agent.persistence.models import FindingRecord, PublicationRecord, WorkUnitRecord
from code_review_agent.persistence import ops
from code_review_agent.providers.local import LocalDiffProvider
from code_review_agent.publishers.dry_run import DryRunPublisher
from code_review_agent.tools.dispatcher import build_dispatcher
from typer.testing import CliRunner

from code_review_agent.cli import app

runner = CliRunner()


class CountingGateway:
    name = "counting"

    def __init__(self, inner, fail_calls=(), exc=RuntimeError):
        self.inner = inner
        self.calls = 0
        self.fail_calls = set(fail_calls)
        self.exc = exc

    def complete(self, request):
        self.calls += 1
        if self.calls in self.fail_calls:
            raise self.exc("injected failure")
        return self.inner.complete(request)


def _pipeline(settings, gateway, session_factory, task_id, tools="configs/tools.yaml"):
    return ReviewPipeline(
        settings=settings,
        gateway=gateway,
        dispatcher=build_dispatcher(tools),
        publisher=DryRunPublisher(),
        provider=LocalDiffProvider(),
        task_id=task_id,
        session_factory=session_factory,
    )


def _config_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "configs" / "agent.yaml"


def test_crash_mid_analysis_resumes_without_redoing_done_units(tmp_path, buggy_diff_path):
    import shutil

    settings_db = tmp_path / "db.sqlite"
    ckpt_db = tmp_path / "db.ckpt.sqlite"  # 与 db 同目录派生，模拟独立文件
    report_dir = tmp_path / "runs"

    from code_review_agent.config import load_settings

    settings = load_settings(_config_path())
    settings.storage.db = str(settings_db)
    settings.storage.report_dir = str(report_dir)
    settings.review.recheck = False

    engine = create_db_engine(settings_db)
    init_db(engine)
    session_factory = make_session_factory(engine)

    task_id = "recovery1"
    crashing = CountingGateway(MockLLMGateway(), fail_calls={2}, exc=KeyboardInterrupt)
    pipeline = _pipeline(settings, crashing, session_factory, task_id)
    graph = build_review_graph(pipeline, checkpointer=create_checkpointer(ckpt_db))
    with pytest.raises(KeyboardInterrupt):
        graph.invoke(
            {"task_id": task_id, "input_ref": str(buggy_diff_path), "source": "local"},
            {"recursion_limit": 100, "configurable": {"thread_id": task_id}},
        )

    # 崩溃后：第 1 个单元已完成并落库（findings 已持久化），第 2 个未完成
    with session_factory() as session:
        units = session.scalars(
            select(WorkUnitRecord).where(WorkUnitRecord.task_id == task_id)
        ).all()
        assert {u.status for u in units} == {"done"}
        assert len(units) == 1
        assert (
            session.scalar(
                select(func.count(FindingRecord.id)).where(FindingRecord.task_id == task_id)
            )
            == 5
        )

    # 模拟重启进程：全新引擎、全新 checkpointer 连接、正常网关
    engine2 = create_db_engine(settings_db)
    session_factory2 = make_session_factory(engine2)
    counting = CountingGateway(MockLLMGateway())
    pipeline2 = _pipeline(settings, counting, session_factory2, task_id)
    graph2 = build_review_graph(pipeline2, checkpointer=create_checkpointer(ckpt_db))
    final = graph2.invoke(
        None, {"recursion_limit": 100, "configurable": {"thread_id": task_id}}
    )

    assert counting.calls == 1  # 只有未完成的单元重新调用了模型
    assert len(final["validated_findings"]) == 7
    assert all(u.status == WorkUnitStatus.done for u in final["work_units"])
    with session_factory2() as session:
        assert (
            session.scalar(
                select(func.count(FindingRecord.id)).where(FindingRecord.task_id == task_id)
            )
            == 7
        )
        assert (
            session.scalar(
                select(func.count(PublicationRecord.id)).where(
                    PublicationRecord.task_id == task_id
                )
            )
            == 1
        )

    # 重复 resume：线程已在 END，直接返回最终状态，不产生重复记录
    final2 = graph2.invoke(
        None, {"recursion_limit": 100, "configurable": {"thread_id": task_id}}
    )
    assert counting.calls == 1
    assert len(final2["validated_findings"]) == 7
    with session_factory2() as session:
        assert (
            session.scalar(
                select(func.count(FindingRecord.id)).where(FindingRecord.task_id == task_id)
            )
            == 7
        )


def _cli_run(args, tmp_path):
    db = tmp_path / "db.sqlite"
    report_dir = tmp_path / "runs"
    full = [
        *args[:2],
        "--config",
        str(_config_path()),
        "--tools",
        str(_config_path().parent / "tools.yaml"),
        "--db",
        str(db),
        "--report-dir",
        str(report_dir),
        *args[2:],
    ]
    return runner.invoke(app, full), db, report_dir


def test_cli_resume_retries_failed_units(tmp_path, buggy_diff_path, monkeypatch):
    import code_review_agent.cli as cli_mod

    flaky = CountingGateway(MockLLMGateway(), fail_calls={2})
    monkeypatch.setattr(cli_mod, "build_gateway", lambda settings: flaky)
    result, db, report_dir = _cli_run(["review", str(buggy_diff_path)], tmp_path)
    assert result.exit_code == 0, result.output

    from code_review_agent.persistence.db import make_session_factory

    sf = make_session_factory(create_db_engine(db))
    with sf() as session:
        from code_review_agent.persistence.models import TaskRecord

        task = session.query(TaskRecord).one()
        assert task.status == "completed"
        task_id = task.id
        failed = session.scalars(
            select(WorkUnitRecord).where(
                WorkUnitRecord.task_id == task_id, WorkUnitRecord.status == "failed"
            )
        ).all()
        assert len(failed) == 1
        finding_count = session.scalar(
            select(func.count(FindingRecord.id)).where(FindingRecord.task_id == task_id)
        )
        assert finding_count == 5  # 失败单元的 findings 缺失

    monkeypatch.undo()
    result2, _, _ = _cli_run(["resume", task_id], tmp_path)
    assert result2.exit_code == 0, result2.output
    assert "恢复任务" in result2.output

    sf2 = make_session_factory(create_db_engine(db))
    with sf2() as session:
        assert (
            session.scalar(
                select(func.count(FindingRecord.id)).where(FindingRecord.task_id == task_id)
            )
            == 7
        )
        assert (
            session.scalars(
                select(WorkUnitRecord).where(
                    WorkUnitRecord.task_id == task_id, WorkUnitRecord.status == "failed"
                )
            ).all()
            == []
        )
        assert (
            session.scalar(
                select(func.count(PublicationRecord.id)).where(
                    PublicationRecord.task_id == task_id
                )
            )
            == 1
        )

    # 再次 resume：已完成且无失败单元 → 幂等退出
    result3, _, _ = _cli_run(["resume", task_id], tmp_path)
    assert result3.exit_code == 0
    assert "已完成" in result3.output
    with sf2() as session:
        assert (
            session.scalar(
                select(func.count(FindingRecord.id)).where(FindingRecord.task_id == task_id)
            )
            == 7
        )


def test_cli_resume_marks_stale_when_input_changed(tmp_path, buggy_diff_path):
    original = buggy_diff_path.read_text(encoding="utf-8")
    diff_copy = tmp_path / "input.diff"
    diff_copy.write_text(original, encoding="utf-8")
    result, db, _ = _cli_run(["review", str(diff_copy)], tmp_path)
    assert result.exit_code == 0, result.output
    from code_review_agent.persistence.db import make_session_factory
    from code_review_agent.persistence.models import TaskRecord

    sf = make_session_factory(create_db_engine(db))
    with sf() as session:
        task_id = session.query(TaskRecord).one().id

    diff_copy.write_text(original + "+extra_line = 1\n", encoding="utf-8")
    result2, _, _ = _cli_run(["resume", task_id], tmp_path)
    assert result2.exit_code == 1
    assert "stale" in result2.output
    with sf() as session:
        task = session.get(TaskRecord, task_id)
        assert task.status == "stale"


def test_cli_resume_after_early_failure_can_succeed(tmp_path, monkeypatch):
    missing = tmp_path / "notyet.diff"
    result, db, _ = _cli_run(["review", str(missing)], tmp_path)
    assert result.exit_code == 1
    from code_review_agent.persistence.db import make_session_factory
    from code_review_agent.persistence.models import TaskRecord

    sf = make_session_factory(create_db_engine(db))
    with sf() as session:
        task = session.query(TaskRecord).one()
        assert task.status == "failed"
        task_id = task.id

    missing.write_text(
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-a\n+b = eval('1')\n",
        encoding="utf-8",
    )
    result2, _, _ = _cli_run(["resume", task_id], tmp_path)
    assert result2.exit_code == 0, result2.output
    with sf() as session:
        task = session.get(TaskRecord, task_id)
        assert task.status == "completed"
