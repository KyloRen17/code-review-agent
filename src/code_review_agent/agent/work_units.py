from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field

from ..diff.models import DiffFile, DiffHunk, FileChangeKind


class WorkUnitStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class WorkUnit(BaseModel):
    unit_id: str
    file: str
    hunk_index: int
    start_line: int
    end_line: int
    content: str
    status: WorkUnitStatus = WorkUnitStatus.pending
    fingerprint: str = ""
    skip_reason: str | None = None


def build_work_units(
    task_id: str, diff_files: list[DiffFile], max_unit_bytes: int
) -> tuple[list[WorkUnit], list[str]]:
    """以 hunk 为审查工作单元；超大 hunk 按行窗口切分为多个 chunk，
    每个 chunk 保留精确的新文件行号映射。"""
    units: list[WorkUnit] = []
    notes: list[str] = []
    for df in diff_files:
        if df.kind == FileChangeKind.binary:
            notes.append(f"跳过 {df.path}: 二进制文件")
            continue
        if df.kind == FileChangeKind.deleted:
            notes.append(f"跳过 {df.path}: 文件被删除，无新代码可审")
            continue
        for idx, hunk in enumerate(df.hunks):
            content = hunk.text()
            if len(content.encode("utf-8")) <= max_unit_bytes:
                unit = _make_unit(task_id, df, idx, hunk, content)
                if unit is None:
                    notes.append(f"跳过 {task_id}:{df.path}#h{idx}: hunk 无新增行")
                else:
                    units.append(unit)
                continue
            chunks = _split_hunk(hunk, max_unit_bytes)
            if not chunks:
                notes.append(f"跳过 {task_id}:{df.path}#h{idx}: 超大 hunk 且无新增行")
                continue
            notes.append(
                f"切分 {task_id}:{df.path}#h{idx}: hunk 超过 {max_unit_bytes} 字节，分为 {len(chunks)} 个 chunk"
            )
            for ci, (chunk_text, start, end) in enumerate(chunks):
                units.append(
                    WorkUnit(
                        unit_id=f"{task_id}:{df.path}#h{idx}c{ci}",
                        file=df.path,
                        hunk_index=idx,
                        start_line=start,
                        end_line=end,
                        content=chunk_text,
                        fingerprint=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:16],
                    )
                )
    return units, notes


def _make_unit(task_id: str, df: DiffFile, idx: int, hunk: DiffHunk, content: str) -> WorkUnit | None:
    rng = hunk.new_line_range()
    if rng is None:
        return None
    return WorkUnit(
        unit_id=f"{task_id}:{df.path}#h{idx}",
        file=df.path,
        hunk_index=idx,
        start_line=rng[0],
        end_line=rng[1],
        content=content,
        fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
    )


def _split_hunk(hunk: DiffHunk, max_unit_bytes: int) -> list[tuple[str, int, int]]:
    """按字节上限把 hunk 行切成若干连续 chunk，返回 (chunk 文本, 起始新行号, 结束新行号)。
    只包含删除行的 chunk 不产出（无新代码可审）。"""
    prefix = {"added": "+", "removed": "-", "context": " "}
    chunks: list[tuple[str, int, int]] = []
    current_lines: list[str] = []
    current_bytes = 0
    start = end = None

    def flush() -> None:
        nonlocal current_lines, current_bytes, start, end
        if current_lines and start is not None:
            chunks.append((hunk.header + "\n" + "\n".join(current_lines), start, end))
        current_lines = []
        current_bytes = 0
        start = end = None

    for line in hunk.lines:
        text = prefix.get(line.kind, " ") + line.content
        if current_bytes + len(text.encode("utf-8")) + 1 > max_unit_bytes and current_lines:
            flush()
        current_lines.append(text)
        current_bytes += len(text.encode("utf-8")) + 1
        if line.new_line is not None:
            if start is None:
                start = line.new_line
            end = line.new_line
    flush()
    return chunks
