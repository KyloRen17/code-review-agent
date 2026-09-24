from __future__ import annotations

import os

import httpx

from .gateway import LLMError, LLMRequest, LLMResponse, Usage


class OpenAICompatGateway:
    """OpenAI 兼容 chat/completions 网关。

    凭证只从环境变量（默认 OPENAI_API_KEY）读取，绝不写入配置文件、日志或报告。
    base_url 可指向任何 OpenAI 兼容服务（vLLM、DashScope、Azure 代理等）。
    """

    name = "openai"

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
        json_mode: bool = True,
        temperature: float = 0.2,
    ) -> None:
        self.model = model_name
        self.api_key = api_key if api_key is not None else os.environ.get(api_key_env)
        if not self.api_key:
            raise LLMError(
                "no_api_key", f"缺少 API Key：请设置环境变量 {api_key_env}（凭证不会写入任何文件）"
            )
        self.base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self._client = client
        self.timeout = timeout
        self.json_mode = json_mode
        self.temperature = temperature

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "code-review-agent",
        }

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": self.temperature,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        client = self._client or self._make_client()
        try:
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise LLMError("network", f"模型服务网络错误: {exc}") from exc
        finally:
            if self._client is None:
                client.close()

        self._raise_for_status(response)
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("invalid_response", f"模型响应结构异常: {exc}") from exc
        return LLMResponse(
            call_id=request.call_id,
            model=self.model,
            content=content,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            finish=choice.get("finish_reason", "unknown"),
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        detail = ""
        try:
            detail = str(response.json().get("error", {}).get("message", ""))
        except Exception:
            detail = response.text[:200]
        if status in (401, 403):
            raise LLMError("auth_failed", f"模型服务鉴权失败: {detail}")
        if status == 429:
            raise LLMError("rate_limited", f"模型服务限流: {detail}")
        raise LLMError("http", f"模型服务 HTTP {status}: {detail}")
