from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


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
