from __future__ import annotations

import os
import re
from urllib.parse import quote

import httpx

from ..providers.gitlab import GitLabMRProvider
from ..review.finding import Finding
from .base import CRA_ANCHOR, PublishReceipt
from .github import added_line_map, render_comment_body


class GitLabMRPublisher:
    """GitLab MR 行级评论发布器（discussions + position）。

    - 只发布能精确定位到**新增行**的 findings；
    - 发布前校验 MR diff_refs head SHA；
    - 远端去重：锚点 `<!-- CRA-FINDING:{id} -->`，发布前拉取既有 discussions。
    """

    mode = "gitlab"

    def __init__(
        self,
        api_base: str = "https://gitlab.com/api/v4",
        token: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token if token is not None else os.environ.get("GITLAB_TOKEN")
        self._client = client
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "code-review-agent"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        return headers

    def publish(
        self,
        *,
        task_id: str,
        report_path: str,
        findings: list[Finding],
        input_ref: str = "",
        base_sha: str | None = None,
        head_sha: str | None = None,
        diff_files: list | None = None,
    ) -> PublishReceipt:
        try:
            project_path, iid = GitLabMRProvider().parse_ref(input_ref)
        except Exception as exc:
            return PublishReceipt(
                mode=self.mode, published=False, failed=True,
                detail=f"无法解析 MR 链接，未发布: {exc}",
            )
        project = quote(project_path, safe="")
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            meta = client.get(
                f"{self.api_base}/projects/{project}/merge_requests/{iid}",
                headers=self._headers(),
            )
            if meta.status_code >= 400:
                return self._error_receipt(meta, f"获取 MR !{iid} 元数据失败")
            diff_refs = meta.json().get("diff_refs") or {}
            current_head = diff_refs.get("head_sha")
            if head_sha and current_head and head_sha != current_head:
                return PublishReceipt(
                    mode=self.mode, published=False, failed=True,
                    detail=f"MR head 已更新（任务基于 {head_sha[:10]}，当前 {current_head[:10]}），拒绝发布以防评论错位",
                )

            existing = client.get(
                f"{self.api_base}/projects/{project}/merge_requests/{iid}/discussions",
                params={"per_page": 100},
                headers=self._headers(),
            )
            if existing.status_code >= 400:
                return self._error_receipt(existing, "获取既有 discussions 失败")
            posted_anchors = set()
            for discussion in existing.json():
                for note in discussion.get("notes", []):
                    m = re.search(r"<!-- CRA-FINDING:(\w+) -->", note.get("body", ""))
                    if m:
                        posted_anchors.add(m.group(1))

            added = added_line_map(diff_files or [])
            posted = skipped = 0
            errors: list[str] = []
            for f in findings:
                if f.finding_id in posted_anchors:
                    skipped += 1
                    continue
                lines = added.get(f.file, set())
                if f.line is None or f.line not in lines:
                    skipped += 1
                    continue
                position = {
                    "position_type": "text",
                    "new_path": f.file,
                    "new_line": f.line,
                    "base_sha": diff_refs.get("base_sha") or base_sha,
                    "start_sha": diff_refs.get("start_sha") or base_sha,
                    "head_sha": current_head or head_sha,
                }
                response = client.post(
                    f"{self.api_base}/projects/{project}/merge_requests/{iid}/discussions",
                    headers=self._headers(),
                    json={"body": render_comment_body(f), "position": position},
                )
                if response.status_code >= 400:
                    errors.append(f"{f.finding_id}: HTTP {response.status_code}")
                    continue
                posted += 1
        except httpx.HTTPError as exc:
            return PublishReceipt(
                mode=self.mode, published=False, failed=True, detail=f"网络错误，未发布: {exc}"
            )
        finally:
            if self._client is None:
                client.close()

        detail = f"发布到 MR !{iid}：新增行级评论 {posted} 条"
        if skipped:
            detail += f"；{skipped} 条无法精确定位或已存在，仅保留在报告 {report_path}"
        if errors:
            detail += f"；失败 {len(errors)} 条（{errors[0]} 等）"
        return PublishReceipt(
            mode=self.mode,
            published=posted > 0,
            posted_comments=posted,
            skipped_comments=skipped,
            failed=bool(errors),
            detail=detail,
        )

    def _error_receipt(self, response: httpx.Response, context: str) -> PublishReceipt:
        if response.status_code == 429:
            return PublishReceipt(mode=self.mode, published=False, failed=True, detail=f"{context}: 速率限制（429）")
        if response.status_code in (401, 403):
            return PublishReceipt(mode=self.mode, published=False, failed=True, detail=f"{context}: 鉴权失败（检查 GITLAB_TOKEN）")
        return PublishReceipt(
            mode=self.mode, published=False, failed=True, detail=f"{context}: HTTP {response.status_code}"
        )
