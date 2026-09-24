from __future__ import annotations

from pathlib import Path

import pytest

from code_review_agent.agent.graph import build_review_graph
from code_review_agent.agent.nodes import ReviewPipeline
from code_review_agent.agent.work_units import WorkUnitStatus
from code_review_agent.config import load_settings
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.budget import BudgetController, BudgetLedger, ModelPrice, PricingTable
from code_review_agent.providers.local import LocalDiffProvider
from code_review_agent.publishers.dry_run import DryRunPublisher
from code_review_agent.tools.dispatcher import build_dispatcher


def _expensive_budget(session_factory, limit: float) -> BudgetController:
    pricing = PricingTable(
        {"mock-reviewer-v1": ModelPrice(input_per_1k=100.0, output_per_1k=0.0)}, "CNY"
    )
    return BudgetController(pricing, BudgetLedger(session_factory, limit, "CNY"))


def test_budget_exhaustion_yields_partial_report_with_unreviewed_scope(
    tmp_path, buggy_diff_path, session_factory, tools_config_path
):
    settings = load_settings(tools_config_path.parent / "agent.yaml")
    settings.storage.report_dir = str(tmp_path / "runs")
    settings.model.max_output_tokens = 100
    # 单价 100 元/1k 输入，每单元实际 ~22.4 元：预算 30 元 → 第一个单元可调用并结算，
    # 第二个单元预留时（22.4+22.4 > 30）被拒，标记 skipped。
    budget = _expensive_budget(session_factory, limit=30.0)
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=MockLLMGateway(),
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=LocalDiffProvider(),
        task_id="budget1",
        session_factory=session_factory,
        budget=budget,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "budget1", "input_ref": str(buggy_diff_path), "source": "local"},
        {"recursion_limit": 100},
    )

    statuses = {u.status for u in final["work_units"]}
    assert statuses == {WorkUnitStatus.done, WorkUnitStatus.skipped}
    done_files = {u.file for u in final["work_units"] if u.status == WorkUnitStatus.done}
    assert done_files == {"app/auth.py"}
    # 已完成单元的结果保留
    assert len(final["validated_findings"]) == 5
    assert any("预算耗尽" in e for e in final["errors"])

    snap = budget.snapshot("budget1")
    assert snap["spent"] > 0
    assert snap["remaining"] == pytest.approx(snap["limit"] - snap["spent"] - snap["reserved"])

    report = Path(final["report_path"]).read_text(encoding="utf-8")
    assert "预算" in report
    assert "未审查" in report
    assert "非服务商精确账单" in report


def test_budget_gate_blocks_call_before_sending(tmp_path, buggy_diff_path, session_factory, tools_config_path):
    """预算为 0 时任何调用都不发起：gateway 记录 0 次调用，全部单元跳过。"""

    class _CountingGateway(MockLLMGateway):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete(self, request):
            self.calls += 1
            return super().complete(request)

    settings = load_settings(tools_config_path.parent / "agent.yaml")
    settings.storage.report_dir = str(tmp_path / "runs")
    gateway = _CountingGateway()
    budget = _expensive_budget(session_factory, limit=0.0)
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=gateway,
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=LocalDiffProvider(),
        task_id="budget0",
        session_factory=session_factory,
        budget=budget,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "budget0", "input_ref": str(buggy_diff_path), "source": "local"},
        {"recursion_limit": 100},
    )
    assert gateway.calls == 0
    assert all(u.status == WorkUnitStatus.skipped for u in final["work_units"])
    assert final["validated_findings"] == []
    report = Path(final["report_path"]).read_text(encoding="utf-8")
    assert "预算耗尽" in report or "未审查" in report
