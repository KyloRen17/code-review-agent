from __future__ import annotations

import difflib
import re
from typing import Iterable

from ..diff.models import DiffFile
from ..tools.base import ToolResult
from .finding import Confidence, Finding

_TITLE_SIMILARITY = 0.8
_LINE_TOLERANCE = 3


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _added_lines(df: DiffFile) -> set[int]:
    added = set()
    for hunk in df.hunks:
        for line in hunk.lines:
            if line.kind == "added" and line.new_line is not None:
                added.add(line.new_line)
    return added


def _hunk_text(df: DiffFile) -> str:
    return "\n".join(hunk.text() for hunk in df.hunks)


def _tool_error_positions_from_state(
    tool_results: Iterable[ToolResult], file_of_unit: dict[str, str]
) -> dict[str, set[int]]:
    positions: dict[str, set[int]] = {}
    for tr in tool_results:
        errors = (tr.output or {}).get("errors") or []
        if not isinstance(errors, list):
            continue
        unit_file = file_of_unit.get(tr.work_unit_id)
        for err in errors:
            if isinstance(err, dict) and isinstance(err.get("line"), int) and unit_file:
                positions.setdefault(unit_file, set()).add(err["line"])
    return positions


def validate_and_grade(
    task_id: str,
    raw: list[Finding],
    diff_files: list[DiffFile],
    tool_results: list[ToolResult] | None = None,
    work_units: list | None = None,
) -> tuple[list[Finding], list[str]]:
    """确定性验证与分级。

    规则（全部可追溯，结果写入 finding 的 downgrade_reason / tool_evidence）：
    1. 文件必须在 diff 内；2. 行号必须落在 hunk 新行范围内；
    3. 高置信度必须：行号指向**新增行**、证据文本确实出现在 diff 中；
    4. 工具独立报告的错误位置 → tool_evidence（有工具佐证且位置可核实 → 允许高置信）；
    5. 精确重复与高相似问题合并（同名文件、行距 ≤3、标题相似度 ≥0.8）。
    LLM 自报 confidence 只是输入信号，最终分级由本函数决定。
    """
    dropped: list[str] = []
    kept: list[Finding] = []
    seen_keys: set[tuple[str, int | None, str]] = set()

    file_map: dict[str, DiffFile] = {}
    for df in diff_files:
        if df.new_path and df.new_path != "/dev/null":
            file_map[df.new_path] = df
        if df.old_path and df.old_path not in ("/dev/null", ""):
            file_map.setdefault(df.old_path, df)

    file_of_unit: dict[str, str] = {}
    for unit in work_units or []:
        file_of_unit[unit.unit_id] = unit.file
    tool_positions = _tool_error_positions_from_state(tool_results or [], file_of_unit)

    for f in raw:
        df = file_map.get(f.file)
        if df is None:
            dropped.append(f"丢弃 {f.finding_id} ({f.title}): 文件 {f.file} 不在本次 diff 中")
            continue
        if f.line is not None:
            in_range = any(
                rng and rng[0] <= f.line <= rng[1] for rng in (h.new_line_range() for h in df.hunks)
            )
            if not in_range:
                dropped.append(f"丢弃 {f.finding_id} ({f.title}): 行号 {f.line} 超出 {f.file} 的变更范围")
                continue

        updates: dict = {}
        reasons: list[str] = []

        tool_hits = tool_positions.get(f.file, set())
        has_tool_evidence = f.line is not None and f.line in tool_hits
        if has_tool_evidence:
            updates["tool_evidence"] = ["py-ast-check"]

        added = _added_lines(df)
        position_is_added = f.line is not None and f.line in added
        evidence_in_diff = bool(f.evidence.strip()) and _norm_text(f.evidence) in _norm_text(_hunk_text(df))

        wants_high = f.confidence == Confidence.high
        can_high = (
            position_is_added
            and evidence_in_diff
            and (f.line is not None)
            and bool(f.evidence.strip())
        ) or has_tool_evidence

        if wants_high and not can_high:
            if f.line is None:
                reasons.append("无法定位到具体行号")
            elif not position_is_added:
                reasons.append("指向未变更行（非本次新增代码）")
            elif not evidence_in_diff:
                reasons.append("证据文本与 diff 不匹配")
            updates["confidence"] = Confidence.reference
        elif not wants_high and has_tool_evidence and can_high:
            updates["confidence"] = Confidence.high
            updates["downgrade_reason"] = None
            reasons.append("工具独立佐证，升级为高置信")

        if reasons and updates.get("confidence") == Confidence.reference:
            updates["downgrade_reason"] = "；".join(reasons)

        key = (f.file, f.line, f.title)
        if key in seen_keys:
            dropped.append(f"丢弃 {f.finding_id} ({f.title}): 与已有发现重复")
            continue
        merged = False
        for existing in kept:
            if (
                existing.file == f.file
                and existing.line is not None
                and f.line is not None
                and abs(existing.line - f.line) <= _LINE_TOLERANCE
                and existing.title != f.title
                and difflib.SequenceMatcher(
                    None, existing.title, f.title
                ).ratio() >= _TITLE_SIMILARITY
            ):
                dropped.append(
                    f"合并 {f.finding_id} ({f.title}) → {existing.finding_id}: 与已有发现高度相似"
                )
                merged = True
                break
        if merged:
            continue
        seen_keys.add(key)
        kept.append(f.model_copy(update=updates) if updates else f)
    return kept, dropped
