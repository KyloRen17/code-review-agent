from __future__ import annotations

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


class ReviewTool:
    name: str = ""
    description: str = ""

    def run(self, context: dict) -> ToolResult:
        raise NotImplementedError
