from __future__ import annotations

from pathlib import Path

import yaml

from .base import ReviewTool, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ReviewTool] = {}

    def register(self, tool: ReviewTool) -> None:
        if not tool.name:
            raise ValueError("工具必须定义非空 name")
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ReviewTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)


_INSTALLED: dict[str, ReviewTool] = {}


def install(tool: type[ReviewTool] | ReviewTool) -> type[ReviewTool] | ReviewTool:
    obj = tool() if isinstance(tool, type) else tool
    if not obj.name:
        raise ValueError(f"工具必须定义非空 name: {tool!r}")
    _INSTALLED[obj.name] = obj
    return tool


def build_registry(tools_yaml: Path | str) -> ToolRegistry:
    """按声明式配置构建工具注册表：enabled 的已安装工具才会进入调度。"""
    from . import implementations  # noqa: F401  触发工具自注册

    p = Path(tools_yaml)
    declared: dict[str, dict] = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        declared = data.get("tools", {}) or {}
    registry = ToolRegistry()
    for name, spec in declared.items():
        if not isinstance(spec, dict) or not spec.get("enabled", False):
            continue
        tool = _INSTALLED.get(name)
        if tool is None:
            continue
        registry.register(tool)
    return registry
