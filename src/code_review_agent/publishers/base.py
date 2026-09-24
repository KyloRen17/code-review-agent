from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..review.finding import Finding


class PublishReceipt(BaseModel):
    mode: str
    published: bool
    detail: str


class ReviewPublisher(Protocol):
    mode: str

    def publish(self, *, task_id: str, report_path: str, findings: list[Finding]) -> PublishReceipt: ...
