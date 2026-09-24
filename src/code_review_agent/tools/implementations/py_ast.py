from __future__ import annotations

import ast

from ..base import BaseReviewTool, ToolResult, ToolScope, ToolStatus
from ..registry import install


@install
class PyAstCheckTool(BaseReviewTool):
    """对本次 diff 全量新增的 Python 文件做 AST 语法检查。

    只解析文本（ast.parse），不执行任何仓库代码。
    修改类文件缺少完整文件内容，AST 不适用，返回 skipped 并说明原因。
    """

    name = "py-ast-check"
    description = "对全量新增的 Python 文件做 AST 语法检查（纯文本解析，不执行代码）"
    scope = ToolScope.unit
    file_types = ["*.py"]
    timeout_s = 10.0
    output_keys = ["syntax_ok", "errors"]

    def run(self, context: dict) -> ToolResult:
        unit = context.get("unit")
        diff_file = context.get("diff_file")
        if unit is None:
            return ToolResult(tool=self.name, status=ToolStatus.skipped, output={"reason": "无工作单元"})
        if diff_file is None or diff_file.kind != "added":
            return ToolResult(
                tool=self.name,
                work_unit_id=unit.unit_id,
                status=ToolStatus.skipped,
                output={"reason": "仅支持全量新增文件；修改类文件缺少完整内容，AST 不适用"},
            )
        if len(diff_file.hunks) != 1:
            return ToolResult(
                tool=self.name,
                work_unit_id=unit.unit_id,
                status=ToolStatus.skipped,
                output={"reason": "文件被分块，AST 检查不适用"},
            )
        code = "\n".join(
            raw[1:] for raw in unit.content.splitlines() if raw.startswith("+")
        )
        try:
            ast.parse(code)
        except SyntaxError as exc:
            line = (unit.start_line or 1) + (exc.lineno or 1) - 1
            return ToolResult(
                tool=self.name,
                work_unit_id=unit.unit_id,
                status=ToolStatus.failure,
                output={
                    "syntax_ok": False,
                    "errors": [{"line": line, "message": exc.msg}],
                },
                error=f"语法错误: 第 {line} 行: {exc.msg}",
            )
        return ToolResult(
            tool=self.name,
            work_unit_id=unit.unit_id,
            status=ToolStatus.success,
            output={"syntax_ok": True, "errors": []},
        )
