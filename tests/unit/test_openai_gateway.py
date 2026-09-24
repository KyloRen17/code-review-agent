from __future__ import annotations

import httpx
import pytest

from code_review_agent.config import AgentSettings, ModelConfig
from code_review_agent.llm import build_gateway
from code_review_agent.llm.gateway import LLMError, LLMRequest
from code_review_agent.llm.openai_compat import OpenAICompatGateway
from code_review_agent.llm.schemas import parse_llm_findings

API_RESPONSE = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": '{"findings": [{"file": "app/auth.py", "line": 3, "title": "问题", "severity": "high", "confidence": "reference", "evidence": "code", "description": "说明", "trigger": "条件", "suggestion": "建议"}]}',
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 45},
}


def _request() -> LLMRequest:
    return LLMRequest(
        call_id="c1", model="gpt-test", system="system", prompt="user", context={"file": "x.py"}
    )


def _gateway(handler, **kwargs) -> OpenAICompatGateway:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatGateway(model_name="gpt-test", api_key="sk-test", client=client, **kwargs)


def test_complete_returns_structured_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json=API_RESPONSE)

    gateway = _gateway(handler, base_url="https://llm.example.com/v1")
    response = gateway.complete(_request())
    assert response.model == "gpt-test"
    assert response.finish == "stop"
    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 45
    parsed = parse_llm_findings(response.content)
    assert parsed.findings[0].trigger == "条件"
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert '"model":"gpt-test"' in captured["body"]
    assert '"role":"system"' in captured["body"]
    assert '"response_format"' in captured["body"]


def test_401_maps_to_auth_failed():
    gateway = _gateway(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError) as excinfo:
        gateway.complete(_request())
    assert excinfo.value.kind == "auth_failed"


def test_429_maps_to_rate_limited():
    gateway = _gateway(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}))
    with pytest.raises(LLMError) as excinfo:
        gateway.complete(_request())
    assert excinfo.value.kind == "rate_limited"


def test_malformed_response_maps_to_invalid_response():
    gateway = _gateway(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMError) as excinfo:
        gateway.complete(_request())
    assert excinfo.value.kind == "invalid_response"


def test_network_error_maps_to_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    gateway = _gateway(handler)
    with pytest.raises(LLMError) as excinfo:
        gateway.complete(_request())
    assert excinfo.value.kind == "network"


def test_missing_api_key_is_rejected_upfront(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError) as excinfo:
        OpenAICompatGateway(model_name="gpt-test", api_key=None)
    assert excinfo.value.kind == "no_api_key"


def test_build_gateway_openai_uses_env_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    settings = AgentSettings(
        model=ModelConfig(provider="openai", name="gpt-test", base_url="https://llm.example.com/v1")
    )
    gateway = build_gateway(settings)
    assert isinstance(gateway, OpenAICompatGateway)
    assert gateway.api_key == "sk-env"
    assert gateway.base_url == "https://llm.example.com/v1"


def test_build_gateway_unknown_provider_rejected():
    settings = AgentSettings(model=ModelConfig(provider="claude-magic", name="x"))
    with pytest.raises(NotImplementedError):
        build_gateway(settings)


def test_json_mode_disabled_omits_response_format():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json=API_RESPONSE)

    gateway = _gateway(handler, json_mode=False)
    gateway.complete(_request())
    assert "response_format" not in captured["body"]
