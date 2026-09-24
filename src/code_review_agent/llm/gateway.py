from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel


class LLMError(Exception):
    """LLM 网关调用失败，带明确状态。"""

    def __init__(self, kind: Literal["auth_failed", "rate_limited", "network", "http", "invalid_response", "no_api_key"], message: str) -> None:
        super().__init__(f"[llm:{kind}] {message}")
        self.kind = kind


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    currency: str = "CNY"
    cost: float | None = None


class LLMRequest(BaseModel):
    call_id: str
    model: str
    system: str
    prompt: str
    context: dict[str, Any] = {}
    max_output_tokens: int = 2048


class LLMResponse(BaseModel):
    call_id: str
    model: str
    content: str
    usage: Usage
    finish: str = "stop"


class LLMGateway(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...
