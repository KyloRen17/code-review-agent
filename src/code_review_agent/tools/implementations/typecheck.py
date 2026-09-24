from __future__ import annotations

from ..base import BaseReviewTool, ToolResult, ToolStatus
from ..registry import install


@install
class TypecheckTool(BaseReviewTool):
    """示例执行型工具。

    运行类型检查必须在沙箱（无网络、非特权、只读输入）内进行。
    沙箱未启用时 Dispatcher 在入口直接拒绝（不会调用本 run）；
    即便被误调度，run 也只返回 skipped，绝不执行宿主机命令。
    """

    name = "typecheck"
    description = "运行 mypy 类型检查（执行型工具；需要沙箱，Phase 9 提供）"
    file_types = ["*.py"]
    requires_execution = True
    timeout_s = 120.0

    def run(self, context: dict) -> ToolResult:
        return ToolResult(
            tool=self.name,
            status=ToolStatus.skipped,
            error="typecheck 是执行型工具：沙箱未就绪（Phase 9），拒绝在宿主机执行任何命令",
        )
