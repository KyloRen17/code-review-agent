from __future__ import annotations

from ..diff.models import DiffFile
from .finding import Confidence, Finding


def validate_and_grade(
    task_id: str, raw: list[Finding], diff_files: list[DiffFile]
) -> tuple[list[Finding], list[str]]:
    """位置校验、去重与置信度降级。

    Phase 1 最小实现：文件必须在 diff 内、行号必须落在 hunk 新行范围内、
    高置信度必须有可定位行号与非空证据。Phase 8 将扩展为证据核查与复核。
    LLM 自报的 confidence 只是输入信号之一，最终分级由本函数决定。
    """
    dropped: list[str] = []
    kept: list[Finding] = []
    seen: set[tuple[str, int | None, str]] = set()

    file_map: dict[str, DiffFile] = {}
    for df in diff_files:
        if df.new_path and df.new_path != "/dev/null":
            file_map[df.new_path] = df
        if df.old_path and df.old_path not in ("/dev/null", ""):
            file_map.setdefault(df.old_path, df)

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
        confidence = f.confidence
        reason: str | None = None
        if confidence == Confidence.high:
            if f.line is None:
                confidence = Confidence.reference
                reason = "无法定位到具体行号，降级为仅供参考"
            elif not f.evidence.strip():
                confidence = Confidence.reference
                reason = "缺少代码证据，降级为仅供参考"
        key = (f.file, f.line, f.title)
        if key in seen:
            dropped.append(f"丢弃 {f.finding_id} ({f.title}): 与已有发现重复")
            continue
        seen.add(key)
        if reason:
            kept.append(f.model_copy(update={"confidence": confidence, "downgrade_reason": reason}))
        else:
            kept.append(f)
    return kept, dropped
