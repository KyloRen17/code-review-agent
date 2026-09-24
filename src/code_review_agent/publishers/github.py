from __future__ import annotations

import os
import re

import httpx

from ..providers.github import GitHubPRProvider
from ..review.finding import Confidence, Finding
from ..security.redactor import redact
from .base import CRA_ANCHOR, PublishReceipt

_BODY_TEMPLATE = """{anchor}
**[{severity}] {title}**（置信度: {confidence}）

{description}

- 触发条件: {trigger}
- 建议: {suggestion}
- 证据: `{evidence}`

<sub>由 code-review-agent 生成；完整证据链: `cra trace {finding_id}`</sub>
"""


def added_line_map(diff_files: list) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for df in diff_files or []:
        added = set()
        for hunk in df.hunks:
            for line in hunk.lines:
                if line.kind == "added" and line.new_line is not None:
                    added.add(line.new_line)
        if df.path in out:
            out[df.path] |= added
        else:
            out[df.path] = added
    return out


def render_comment_body(finding: Finding) -> str:
    evidence, _ = redact((finding.evidence or "—").replace("\n", " ")[:200])
    return _BODY_TEMPLATE.format(
        anchor=CRA_ANCHOR.format(finding_id=finding.finding_id),
        severity=finding.severity.value.upper(),
        title=finding.title,
        confidence="高（可直接采纳）" if finding.confidence == Confidence.high else "仅供参考",
        description=finding.description,
        trigger=finding.trigger or "—",
        suggestion=finding.suggestion or "—",
        evidence=evidence,
        finding_id=finding.finding_id,
    )


class GitHubReviewPublisher:
    """GitHub PR 行级评论发布器。

    - 只发布能精确定位到**新增行**的 findings；无法定位的仅保留在 Markdown 报告；
    - 发布前校验 PR head SHA（不匹配则拒绝，防评论贴到过期 diff）；
    - 远端去重：评论 body 内嵌稳定锚点 `<!-- CRA-FINDING:{id} -->`，
      发布前先拉取既有评论，已存在锚点的跳过（覆盖"远端已成功、本地记账中断"场景）。
    """

    mode = "github"

    def __init__(
        self,
        api_base: str = "https://api.github.com",
        token: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._client = client
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "code-review-agent",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
            owner, repo, number = GitHubPRProvider().parse_ref(input_ref)
        except Exception as exc:
            return PublishReceipt(
                mode=self.mode, published=False, failed=True,
                detail=f"无法解析 PR 链接，未发布: {exc}",
            )
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            meta = client.get(
                f"{self.api_base}/repos/{owner}/{repo}/pulls/{number}",
                headers=self._headers(),
            )
            if meta.status_code >= 400:
                return self._error_receipt(meta, f"获取 PR #{number} 元数据失败")
            current_head = meta.json().get("head", {}).get("sha")
            if head_sha and current_head and head_sha != current_head:
                return PublishReceipt(
                    mode=self.mode, published=False, failed=True,
                    detail=f"PR head 已更新（任务基于 {head_sha[:10]}，当前 {current_head[:10]}），拒绝发布以防评论错位",
                )

            existing = client.get(
                f"{self.api_base}/repos/{owner}/{repo}/pulls/{number}/comments",
                params={"per_page": 100},
                headers=self._headers(),
            )
            if existing.status_code >= 400:
                return self._error_receipt(existing, "获取既有评论失败")
            posted_anchors = {
                m.group(1)
                for body in [c.get("body", "") for c in existing.json()]
                for m in [re.search(r"<!-- CRA-FINDING:(\w+) -->", body)]
                if m
            }

            added = added_line_map(diff_files or [])
            posted = skipped = 0
            errors: list[str] = []
            for f in findings:
                if f.finding_id in posted_anchors:
                    skipped += 1
                    continue
                lines = added.get(f.file, set())
                if f.line is None or f.line not in lines:
                    skipped += 1  # 无法精确定位 → 仅保留在报告
                    continue
                response = client.post(
                    f"{self.api_base}/repos/{owner}/{repo}/pulls/{number}/comments",
                    headers=self._headers(),
                    json={
                        "body": render_comment_body(f),
                        "path": f.file,
                        "line": f.line,
                        "side": "RIGHT",
                        "commit_id": current_head,
                    },
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

        detail = f"发布到 PR #{number}：新增行级评论 {posted} 条"
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
            return PublishReceipt(mode=self.mode, published=False, failed=True, detail=f"{context}: 鉴权失败（检查 GITHUB_TOKEN）")
        return PublishReceipt(
            mode=self.mode, published=False, failed=True, detail=f"{context}: HTTP {response.status_code}"
        )
