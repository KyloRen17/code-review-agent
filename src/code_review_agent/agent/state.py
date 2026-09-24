from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from ..diff.models import DiffFile
from ..security.redactor import RedactionReport
from ..tools.base import ToolResult
from .work_units import WorkUnit
from ..review.finding import Finding


class GraphState(TypedDict, total=False):
    task_id: str
    input_ref: str
    source: str

    # 原始 diff 永不进入 state（checkpoint 会落盘未脱敏内容）；只携带脱敏后的文本
    redacted_diff: str
    input_fingerprint: str
    base_sha: str | None
    head_sha: str | None

    redaction: RedactionReport
    diff_files: list[DiffFile]
    work_units: list[WorkUnit]
    unit_notes: list[str]

    tool_selection: list[str]
    tool_results: list[ToolResult]

    findings: list[Finding]
    validated_findings: list[Finding]
    dropped_notes: list[str]
    usage_total: dict

    report_path: str
    publish_receipt: dict

    errors: Annotated[list[str], operator.add]
    node_trace: Annotated[list[str], operator.add]
