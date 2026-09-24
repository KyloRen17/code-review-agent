from __future__ import annotations

from ..base import BaseReviewTool, ToolResult, ToolScope, ToolStatus
from ..registry import install

_REFUSAL = "typecheck 是执行型工具：沙箱不可用或未启用，拒绝在宿主机执行任何命令"


@install
class TypecheckTool(BaseReviewTool):
    """示例执行型工具：在全量新增的 Python 文件上做编译检查。

    编译检查通过受限沙箱执行（无网络/非特权/只读/资源上限）：
    - 沙箱不可用 → Dispatcher 入口直接拒绝（不会调用本 run）；
    - 即便被误调度，run 内部再次校验沙箱并返回 skipped，绝不执行宿主机命令；
    - 命令固定为容器内的纯 compile（不 import、不执行被审代码逻辑），
      diff/模型内容无法注入或拼接任意 shell。
    """

    name = "typecheck"
    description = "沙箱内对全量新增 Python 文件做编译检查（执行型工具）"
    scope = ToolScope.unit
    file_types = ["*.py"]
    requires_execution = True
    timeout_s = 120.0
    output_keys = ["compiled", "error"]

    def run(self, context: dict) -> ToolResult:
        sandbox = context.get("sandbox")
        unit = context.get("unit")
        diff_file = context.get("diff_file")
        if sandbox is None or not getattr(sandbox, "available", False):
            return ToolResult(tool=self.name, status=ToolStatus.skipped, error=_REFUSAL)
        if unit is None or diff_file is None or diff_file.kind != "added" or len(diff_file.hunks) != 1:
            return ToolResult(
                tool=self.name,
                work_unit_id=unit.unit_id if unit else "*",
                status=ToolStatus.skipped,
                output={"reason": "仅支持全量新增文件（修改类文件缺少完整内容）"},
            )
        code = "\n".join(raw[1:] for raw in unit.content.splitlines() if raw.startswith("+"))
        command = [
            "python",
            "-c",
            "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')",
            "src.py",
        ]
        try:
            result = sandbox.run(command, timeout_s=self.timeout_s, files={"src.py": code})
        except Exception as exc:
            return ToolResult(
                tool=self.name,
                work_unit_id=unit.unit_id,
                status=ToolStatus.failure,
                error=f"沙箱执行失败: {exc}",
            )
        if result.exit_code == 0:
            return ToolResult(
                tool=self.name,
                work_unit_id=unit.unit_id,
                status=ToolStatus.success,
                output={"compiled": True},
            )
        return ToolResult(
            tool=self.name,
            work_unit_id=unit.unit_id,
            status=ToolStatus.failure,
            output={"compiled": False},
            error=result.stderr.strip()[:500] or "编译失败",
        )
