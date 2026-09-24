from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class Confidence(str, Enum):
    high = "high"          # 高置信度，可直接采纳
    reference = "reference"  # 仅供参考


class Finding(BaseModel):
    finding_id: str
    task_id: str
    file: str
    line: int | None = Field(default=None, ge=1)
    title: str
    description: str
    trigger: str | None = None
    evidence: str = ""
    suggestion: str | None = None
    severity: Severity = Severity.info
    confidence: Confidence = Confidence.reference
    origin: str = ""
    downgrade_reason: str | None = None
    tool_evidence: list[str] = Field(default_factory=list)
    recheck: dict | None = None
