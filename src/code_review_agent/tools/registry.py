from __future__ import annotations

from pathlib import Path

import yaml

from .base import BaseReviewTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseReviewTool] = {}

    def register(self, tool: BaseReviewTool) -> None:
        if not tool.name:
            raise ValueError("工具必须定义非空 name")
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseReviewTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)


_INSTALLED: dict[str, BaseReviewTool] = {}


def install(tool: type[BaseReviewTool] | BaseReviewTool) -> type[BaseReviewTool] | BaseReviewTool:
    obj = tool() if isinstance(tool, type) else tool
    if not obj.name:
        raise ValueError(f"工具必须定义非空 name: {tool!r}")
    _INSTALLED[obj.name] = obj
    return tool


def load_tools_config(path: Path | str | None) -> dict:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}
