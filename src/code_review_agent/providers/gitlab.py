from __future__ import annotations

import hashlib
import os
import re
from urllib.parse import quote

import httpx

from .base import ProviderError, ReviewInput

_MR_URL_RE = re.compile(
    r"^https?://([^/]+)/([^/]+(?:/[^/]+)*?)/-/merge_requests/(\d+)/?$"
)


class GitLabMRProvider:
    source = "gitlab"

    def __init__(
        self,
        api_base: str = "https://gitlab.com/api/v4",
        token: str | None = None,
        client: httpx.Client | None = None,
        max_bytes: int = 5 * 1024 * 1024,
        timeout: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token if token is not None else os.environ.get("GITLAB_TOKEN")
        self._client = client
        self.max_bytes = max_bytes
        self.timeout = timeout

    def parse_ref(self, ref: str) -> tuple[str, str]:
        m = _MR_URL_RE.match(ref.strip())
        if not m:
            raise ProviderError("invalid_input", f"无法解析 GitLab MR 链接: {ref}")
        return m.group(2), m.group(3)

    def _make_client(self) -> httpx.Client:
        headers = {"User-Agent": "code-review-agent"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        return httpx.Client(headers=headers, timeout=self.timeout)

    def _auth_headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token} if self.token else {}

    def fetch(self, ref: str) -> ReviewInput:
        project_path, iid = self.parse_ref(ref)
        project = quote(project_path, safe="")
        client = self._client or self._make_client()
        base = self.api_base
        auth = self._auth_headers()
        try:
            meta_response = client.get(
                f"{base}/projects/{project}/merge_requests/{iid}", headers=auth
            )
            self._raise_for_status(meta_response, f"获取 MR !{iid} 元数据失败")
            meta = meta_response.json()

            changes_response = client.get(
                f"{base}/projects/{project}/merge_requests/{iid}/changes", headers=auth
            )
            self._raise_for_status(changes_response, f"获取 MR !{iid} 变更失败")
            changes = changes_response.json().get("changes", [])
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError("network", f"GitLab API 网络错误: {exc}") from exc
        finally:
            if self._client is None:
                client.close()

        diff = self._reconstruct_diff(changes)
        if len(diff.encode("utf-8")) > self.max_bytes:
            raise ProviderError(
                "too_large", f"MR diff 大小超过上限 {self.max_bytes} 字节，拒绝处理"
            )
        if not diff.strip():
            raise ProviderError("not_found", f"MR !{iid} 没有可审查的变更")

        diff_refs = meta.get("diff_refs") or {}
        return ReviewInput(
            source="gitlab",
            raw_diff=diff,
            fingerprint=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            base_sha=diff_refs.get("base_sha"),
            head_sha=diff_refs.get("head_sha"),
            title=meta.get("title"),
            description=meta.get("description"),
        )

    @staticmethod
    def _reconstruct_diff(changes: list[dict]) -> str:
        """GitLab changes 接口按文件返回 diff 片段（不含 `diff --git` 头），
        重建为标准 unified diff 以复用同一解析器。"""
        out: list[str] = []
        for change in changes:
            old_path = change.get("old_path") or change.get("new_path") or "unknown"
            new_path = change.get("new_path") or change.get("old_path") or "unknown"
            old_marker = "--- /dev/null" if change.get("new_file") else f"--- a/{old_path}"
            new_marker = "+++ /dev/null" if change.get("deleted_file") else f"+++ b/{new_path}"
            out.append(f"diff --git a/{old_path} b/{new_path}")
            if change.get("new_file"):
                out.append("new file mode 100644")
            if change.get("deleted_file"):
                out.append("deleted file mode 100644")
            out.append(old_marker)
            out.append(new_marker)
            out.append(change.get("diff", ""))
        return "\n".join(out)

    @staticmethod
    def _raise_for_status(response: httpx.Response, context: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise ProviderError("not_found", f"{context}: 资源不存在或无权访问")
        if status == 401:
            raise ProviderError("auth_failed", f"{context}: 令牌缺失或无效（检查 GITLAB_TOKEN）")
        if status == 403:
            raise ProviderError("auth_failed", f"{context}: 权限不足")
        if status == 429:
            raise ProviderError("rate_limited", f"{context}: 请求过多被限流")
        raise ProviderError("http", f"{context}: HTTP {status} {response.reason_phrase}")
