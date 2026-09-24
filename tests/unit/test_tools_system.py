from __future__ import annotations

import time
from pathlib import Path

import pytest

from code_review_agent.diff import parse_unified_diff
from code_review_agent.tools.base import BaseReviewTool, ToolResult, ToolScope, ToolStatus
from code_review_agent.tools.dispatcher import ToolDispatcher, build_dispatcher
from code_review_agent.tools.registry import ToolRegistry, install

SYNTAX_DIFF = (Path(__file__).resolve().parents[2] / "examples" / "syntax_error.diff").read_text(
    encoding="utf-8"
)


class _EchoTool(BaseReviewTool):
    name = "echo-tool"
    description = "test tool"
    scope = ToolScope.task

    def __init__(self, output=None):
        super().__init__()
        self.calls = 0
        self._output = output

    def run(self, context: dict) -> ToolResult:
        self.calls += 1
        if self._output is not None:
            return self._output
        return ToolResult(tool=self.name, output={"echo": True})


class _FlakyTool(BaseReviewTool):
    name = "flaky-tool"
    scope = ToolScope.task

    def __init__(self, failures_before_success=1):
        super().__init__()
        self.calls = 0
        self.failures = failures_before_success

    def run(self, context: dict) -> ToolResult:
        self.calls += 1
        if self.calls <= self.failures:
            return ToolResult(tool=self.name, status=ToolStatus.failure, error="transient")
        return ToolResult(tool=self.name, output={"ok": True})


class _RaisingTool(BaseReviewTool):
    name = "raising-tool"
    scope = ToolScope.task

    def run(self, context: dict) -> ToolResult:
        raise RuntimeError("boom")


class _SlowTool(BaseReviewTool):
    name = "slow-tool"
    scope = ToolScope.task
    timeout_s = 0.1

    def run(self, context: dict) -> ToolResult:
        time.sleep(0.5)
        return ToolResult(tool=self.name, output={"late": True})


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def test_yaml_enabled_tools_are_dispatched(tools_config_path):
    dispatcher = build_dispatcher(tools_config_path)
    assert "diff-stat" in dispatcher.enabled_names()
    assert "py-ast-check" in dispatcher.enabled_names()
    assert "typecheck" not in dispatcher.enabled_names()  # enabled: false


def test_new_tool_registration_without_mainflow_changes(tmp_path, monkeypatch):
    from code_review_agent.tools import registry as reg

    tool = _EchoTool()
    monkeypatch.setitem(reg._INSTALLED, "echo-tool", tool)
    yaml = tmp_path / "tools.yaml"
    yaml.write_text(
        "tools:\n  echo-tool:\n    enabled: true\n    timeout_s: 3\n", encoding="utf-8"
    )
    dispatcher = build_dispatcher(yaml)
    assert dispatcher.enabled_names() == ["echo-tool"]
    assert tool.timeout_s == 3.0  # yaml 覆盖类默认值
    results = dispatcher.run_all(task_id="t", diff_files=[], work_units=[])
    assert results[0].output == {"echo": True}


def test_typecheck_execution_gate_refuses_without_sandbox(tmp_path, monkeypatch):
    from code_review_agent.tools import registry as reg
    from code_review_agent.tools.implementations.typecheck import TypecheckTool

    monkeypatch.setitem(reg._INSTALLED, "typecheck", TypecheckTool())
    yaml = tmp_path / "tools.yaml"
    yaml.write_text(
        "sandbox:\n  enabled: false\ntools:\n  typecheck:\n    enabled: true\n",
        encoding="utf-8",
    )
    dispatcher = build_dispatcher(yaml)
    results = dispatcher.run_all(task_id="t", diff_files=[], work_units=[])
    assert results[0].status == ToolStatus.skipped
    assert "沙箱" in results[0].error
    assert "拒绝" in results[0].error


def test_tool_failure_is_isolated_and_retried():
    flaky = _FlakyTool(failures_before_success=1)
    raising = _RaisingTool()
    echo = _EchoTool()
    dispatcher = ToolDispatcher(_registry(flaky, raising, echo), retries=1)
    results = dispatcher.run_all(task_id="t", diff_files=[], work_units=[])
    by_tool = {r.tool: r for r in results}
    assert by_tool["flaky-tool"].status == ToolStatus.success
    assert by_tool["flaky-tool"].attempts == 2  # 失败一次 + 重试成功
    assert by_tool["raising-tool"].status == ToolStatus.failure
    assert "RuntimeError" in by_tool["raising-tool"].error
    assert by_tool["echo-tool"].status == ToolStatus.success  # 其他工具不受影响


def test_timeout_returns_timeout_status():
    dispatcher = ToolDispatcher(_registry(_SlowTool()), retries=1)
    start = time.monotonic()
    results = dispatcher.run_all(task_id="t", diff_files=[], work_units=[])
    elapsed = time.monotonic() - start
    assert results[0].status == ToolStatus.timeout
    assert "0.1" in results[0].error
    assert elapsed < 1.0  # 超时不重试，不会等满两个 timeout


def test_unit_scope_runs_per_matching_file():
    class _UnitTool(BaseReviewTool):
        name = "unit-tool"
        scope = ToolScope.unit
        file_types = ["*.py"]

        def run(self, context: dict) -> ToolResult:
            return ToolResult(tool=self.name, work_unit_id=context["unit"].unit_id, output={"file": context["unit"].file})

    from code_review_agent.agent.work_units import build_work_units

    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n- old\n+new\n"
        "diff --git a/notes.txt b/notes.txt\n--- a/notes.txt\n+++ b/notes.txt\n@@ -1,1 +1,2 @@\n- old\n+new\n"
    )
    files = parse_unified_diff(diff)
    units, _ = build_work_units("t", files, max_unit_bytes=65536)
    dispatcher = ToolDispatcher(_registry(_UnitTool()))
    results = dispatcher.run_all(task_id="t", diff_files=files, work_units=units)
    assert len(results) == 1  # .txt 被文件类型过滤
    assert results[0].output["file"] == "x.py"


def test_py_ast_check_detects_syntax_error():
    from code_review_agent.agent.work_units import build_work_units
    from code_review_agent.tools.implementations.py_ast import PyAstCheckTool

    files = parse_unified_diff(SYNTAX_DIFF)
    units, _ = build_work_units("t", files, max_unit_bytes=65536)
    dispatcher = ToolDispatcher(_registry(PyAstCheckTool()))
    results = dispatcher.run_all(task_id="t", diff_files=files, work_units=units)
    assert results[0].status == ToolStatus.failure
    assert results[0].output["syntax_ok"] is False
    assert results[0].output["errors"][0]["line"] == 3


def test_py_ast_check_skips_modified_file():
    from code_review_agent.agent.work_units import build_work_units
    from code_review_agent.tools.implementations.py_ast import PyAstCheckTool

    files = parse_unified_diff(SYNTAX_DIFF)
    files[0].kind = "modified"  # 模拟修改类文件
    units, _ = build_work_units("t", files, max_unit_bytes=65536)
    dispatcher = ToolDispatcher(_registry(PyAstCheckTool()))
    results = dispatcher.run_all(task_id="t", diff_files=files, work_units=units)
    assert results[0].status == ToolStatus.skipped
    assert "全量新增" in results[0].output["reason"]


def test_diff_stat_tool_counts(buggy_diff):
    from code_review_agent.tools.implementations.diff_stat import DiffStatTool

    files = parse_unified_diff(buggy_diff)
    result = DiffStatTool().run({"diff_files": files})
    assert result.output["files"] == 2
    assert result.output["added_lines"] == 25
    assert result.output["removed_lines"] == 1


def test_duplicate_registration_rejected():
    r = ToolRegistry()
    r.register(_EchoTool())
    with pytest.raises(ValueError):
        r.register(_EchoTool())
