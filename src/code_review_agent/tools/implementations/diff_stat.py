from __future__ import annotations

from ..base import ReviewTool, ToolResult, ToolStatus
from ..registry import install


@install
class DiffStatTool(ReviewTool):
    name = "diff-stat"
    description = "统计 diff 的文件与行变更数（纯文本计算，不执行任何仓库代码）"

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
