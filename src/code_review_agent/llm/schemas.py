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
    trigger: str | None = None
    suggestion: str | None = None


class LLMFindingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[LLMFinding] = []


class RecheckVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["confirmed", "rejected", "uncertain"]
    reason: str = ""


def parse_llm_findings(text: str) -> LLMFindingOutput:
    cleaned = _strip_fence(text)
    return LLMFindingOutput.model_validate_json(cleaned)


def parse_recheck_verdict(text: str) -> RecheckVerdict:
    return RecheckVerdict.model_validate_json(_strip_fence(text))


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return cleaned
