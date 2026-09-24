from .gateway import LLMError, LLMGateway, LLMRequest, LLMResponse, Usage
from .mock import MockLLMGateway
from .openai_compat import OpenAICompatGateway
from .schemas import LLMFinding, LLMFindingOutput, parse_llm_findings


def build_gateway(settings) -> LLMGateway:
    provider = settings.model.provider
    if provider == "mock":
        return MockLLMGateway(model_name=settings.model.name)
    if provider == "openai":
        return OpenAICompatGateway(
            model_name=settings.model.name,
            base_url=settings.model.base_url,
            api_key_env=settings.model.api_key_env,
            temperature=settings.model.temperature,
        )
    raise NotImplementedError(f"未知 LLM provider: '{provider}'（可选: mock | openai）")


__all__ = [
    "LLMError",
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "Usage",
    "MockLLMGateway",
    "OpenAICompatGateway",
    "LLMFinding",
    "LLMFindingOutput",
    "parse_llm_findings",
    "build_gateway",
]
