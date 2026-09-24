from .gateway import LLMGateway, LLMRequest, LLMResponse, Usage
from .mock import MockLLMGateway
from .schemas import LLMFinding, LLMFindingOutput, parse_llm_findings


def build_gateway(settings) -> LLMGateway:
    provider = settings.model.provider
    if provider == "mock":
        return MockLLMGateway(model_name=settings.model.name)
    raise NotImplementedError(
        f"真实 LLM provider '{provider}' 将在 Phase 3 接入；当前请使用 mock（configs/agent.yaml: model.provider: mock）"
    )


__all__ = [
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "Usage",
    "MockLLMGateway",
    "LLMFinding",
    "LLMFindingOutput",
    "parse_llm_findings",
    "build_gateway",
]
