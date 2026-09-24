from __future__ import annotations

import hashlib
import os
import re

import httpx

from .base import ProviderError, ReviewInput

_PR_URL_RE = re.compile(r"^https?://(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+)/pull/(\d+)/?$")


class GitHubPRProvider:
    source = "github"

    def __init__(
        self,
        api_base: str = "https://api.github.com",
        token: str | None = None,
        client: httpx.Client | None = None,
        max_bytes: int = 5 * 1024 * 1024,
        timeout: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._client = client
        self.max_bytes = max_bytes
        self.timeout = timeout

    def parse_ref(self, ref: str) -> tuple[str, str, int]:
        m = _PR_URL_RE.match(ref.strip())
        if not m:
            raise ProviderError("invalid_input", f"无法解析 GitHub PR 链接: {ref}")
        return m.group(1), m.group(2), int(m.group(3))

    def _make_client(self) -> httpx.Client:
        headers = {
            "User-Agent": "code-review-agent",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return httpx.Client(headers=headers, timeout=self.timeout)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def fetch(self, ref: str) -> ReviewInput:
        owner, repo, number = self.parse_ref(ref)
        client = self._client or self._make_client()
        base = self.api_base
        try:
            meta_response = client.get(
                f"{base}/repos/{owner}/{repo}/pulls/{number}",
                headers={**self._auth_headers(), "Accept": "application/vnd.github+json"},
            )
            self._raise_for_status(meta_response, f"获取 PR #{number} 元数据失败")
            meta = meta_response.json()

            diff_response = client.get(
                f"{base}/repos/{owner}/{repo}/pulls/{number}",
                headers={**self._auth_headers(), "Accept": "application/vnd.github.diff"},
            )
            self._raise_for_status(diff_response, f"获取 PR #{number} diff 失败")
            diff = diff_response.text
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError("network", f"GitHub API 网络错误: {exc}") from exc
        finally:
            if self._client is None:
                client.close()

        if len(diff.encode("utf-8")) > self.max_bytes:
            raise ProviderError(
                "too_large",
                f"PR diff 大小超过上限 {self.max_bytes} 字节，拒绝处理",
            )
        if not diff.strip():
            raise ProviderError("not_found", f"PR #{number} 没有可审查的变更")

        return ReviewInput(
            source="github",
            raw_diff=diff,
            fingerprint=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            base_sha=meta.get("base", {}).get("sha"),
            head_sha=meta.get("head", {}).get("sha"),
            title=meta.get("title"),
            description=meta.get("body"),
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response, context: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise ProviderError("not_found", f"{context}: 资源不存在或无权访问")
        if status in (401, 403) and response.headers.get("x-ratelimit-remaining") == "0":
            raise ProviderError("rate_limited", f"{context}: 触发 GitHub API 速率限制")
        if status in (401, 403):
            raise ProviderError("auth_failed", f"{context}: 令牌缺失或权限不足（检查 GITHUB_TOKEN）")
        if status == 429:
            raise ProviderError("rate_limited", f"{context}: 请求过多被限流")
        raise ProviderError("http", f"{context}: HTTP {status} {response.reason_phrase}")
