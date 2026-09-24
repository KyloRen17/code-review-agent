from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..review.finding import Finding

CRA_ANCHOR = "<!-- CRA-FINDING:{finding_id} -->"


class PublishReceipt(BaseModel):
    mode: str
    published: bool
    detail: str
    posted_comments: int = 0
    skipped_comments: int = 0
    failed: bool = False


class ReviewPublisher(Protocol):
    mode: str

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
    ) -> PublishReceipt: ...
