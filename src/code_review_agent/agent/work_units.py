from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field

from ..diff.models import DiffFile, FileChangeKind


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
            unit_id = f"{task_id}:{df.path}#h{idx}"
            if len(content.encode("utf-8")) > max_unit_bytes:
                notes.append(f"跳过 {unit_id}: hunk 超过大小上限 {max_unit_bytes} 字节")
                continue
            rng = hunk.new_line_range()
            if rng is None:
                notes.append(f"跳过 {unit_id}: hunk 无新增行")
                continue
            units.append(
                WorkUnit(
                    unit_id=unit_id,
                    file=df.path,
                    hunk_index=idx,
                    start_line=rng[0],
                    end_line=rng[1],
                    content=content,
                    fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                )
            )
    return units, notes
