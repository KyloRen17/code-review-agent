from __future__ import annotations

from pathlib import Path

from ..review.finding import Finding
from .base import PublishReceipt


class DryRunPublisher:
    """默认发布器：不产生任何远程写入，只确认本地报告就绪。"""

    mode = "dry_run"

    def publish(
        self,
        *,
        task_id: str,
        report_path: str,
        findings: list[Finding],
        input_ref: str = "",
        base_sha: str | None = None,
        head_sha: str | None = None,
        diff_files: list | None = None,
    ) -> PublishReceipt:
        exists = Path(report_path).is_file()
        detail = (
            f"报告已生成: {report_path}；未执行任何远程写入（远程发布需 --publish 显式授权）"
            if exists
            else f"报告文件缺失: {report_path}"
        )
        return PublishReceipt(mode=self.mode, published=False, detail=detail)
