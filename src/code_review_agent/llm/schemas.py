from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    line: int | None = Field(default=None, ge=1)
    title: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    confidence: Literal["high", "reference"]
    evidence: str = ""
    description: str
    suggestion: str | None = None


class LLMFindingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[LLMFinding] = []


def parse_llm_findings(text: str) -> LLMFindingOutput:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return LLMFindingOutput.model_validate_json(cleaned)
