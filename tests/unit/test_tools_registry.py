from __future__ import annotations

import pytest

from code_review_agent.diff import parse_unified_diff
from code_review_agent.tools.base import ReviewTool, ToolResult
from code_review_agent.tools.registry import build_registry


class _EchoTool(ReviewTool):
    name = "echo-tool"
    description = "test tool"

    def run(self, context: dict) -> ToolResult:
        return ToolResult(tool=self.name, output={"echo": True})


def test_registry_discovers_enabled_declared_tool(tools_config_path):
    registry = build_registry(tools_config_path)
    assert "diff-stat" in registry.names()
    assert "typecheck" not in registry.names()  # enabled: false，不进入调度


def test_new_tool_registration_without_mainflow_changes(tmp_path, monkeypatch):
    from code_review_agent.tools import registry as reg

    monkeypatch.setitem(reg._INSTALLED, "echo-tool", _EchoTool())
    yaml = tmp_path / "tools.yaml"
    yaml.write_text(
        "tools:\n  echo-tool:\n    enabled: true\n    description: test\n", encoding="utf-8"
    )
    registry = build_registry(yaml)
    assert registry.names() == ["echo-tool"]
    result = registry.get("echo-tool").run({})
    assert result.output == {"echo": True}


def test_disabled_tool_not_registered(tmp_path, monkeypatch):
    from code_review_agent.tools import registry as reg

    monkeypatch.setitem(reg._INSTALLED, "echo-tool", _EchoTool())
    yaml = tmp_path / "tools.yaml"
    yaml.write_text("tools:\n  echo-tool:\n    enabled: false\n", encoding="utf-8")
    assert build_registry(yaml).names() == []


def test_duplicate_registration_rejected():
    from code_review_agent.tools.registry import ToolRegistry

    r = ToolRegistry()
    r.register(_EchoTool())
    with pytest.raises(ValueError):
        r.register(_EchoTool())


def test_diff_stat_tool_counts(buggy_diff):
    from code_review_agent.tools.implementations.diff_stat import DiffStatTool

    files = parse_unified_diff(buggy_diff)
    result = DiffStatTool().run({"diff_files": files})
    assert result.output["files"] == 2
    assert result.output["added_lines"] == 25
    assert result.output["removed_lines"] == 1
