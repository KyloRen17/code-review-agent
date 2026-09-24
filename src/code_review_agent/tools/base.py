from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    success = "success"
    failure = "failure"
    timeout = "timeout"
    skipped = "skipped"


class ToolResult(BaseModel):
    tool: str
    work_unit_id: str = "*"
    status: ToolStatus = ToolStatus.success
    output: dict = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0
    attempts: int = 1


class ToolScope(str, Enum):
    task = "task"  # 整个 diff 运行一次
    unit = "unit"  # 每个工作单元运行一次


class BaseReviewTool(ABC):
    """所有工具的统一接口。

    新增工具 = 继承本类 + @install 注册 + 在 configs/tools.yaml 声明 enabled，
    主流程（节点/Dispatcher）零改动。
    """

    name: str = ""
    description: str = ""
    scope: ToolScope = ToolScope.task
    file_types: list[str] = ["*"]
    requires_execution: bool = False  # 执行型工具：必须运行在沙箱内
    timeout_s: float = 30.0
    input_keys: list[str] = []
    output_keys: list[str] = []

    @abstractmethod
    def run(self, context: dict) -> ToolResult: ...
