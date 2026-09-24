from __future__ import annotations

from ..base import BaseReviewTool, ToolResult, ToolScope, ToolStatus
from ..registry import install


@install
class DiffStatTool(BaseReviewTool):
    name = "diff-stat"
    description = "统计 diff 的文件与行变更数（纯文本计算，不执行任何仓库代码）"
    scope = ToolScope.task
    timeout_s = 5.0
    output_keys = ["files", "added_lines", "removed_lines"]

    def run(self, context: dict) -> ToolResult:
        files = context.get("diff_files", [])
        added = removed = 0
        for f in files:
            for h in f.hunks:
                for line in h.lines:
                    if line.kind == "added":
                        added += 1
                    elif line.kind == "removed":
                        removed += 1
        return ToolResult(
            tool=self.name,
            status=ToolStatus.success,
            output={"files": len(files), "added_lines": added, "removed_lines": removed},
        )
