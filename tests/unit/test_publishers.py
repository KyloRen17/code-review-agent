from __future__ import annotations

import json

import httpx
import pytest

from code_review_agent.diff import parse_unified_diff
from code_review_agent.publishers import build_review_publisher
from code_review_agent.publishers.base import PublishReceipt
from code_review_agent.publishers.dry_run import DryRunPublisher
from code_review_agent.publishers.gitlab import GitLabMRPublisher
from code_review_agent.publishers.github import GitHubReviewPublisher
from code_review_agent.review.finding import Confidence, Finding, Severity

PR_URL = "https://github.com/acme/widgets/pull/42"
MR_URL = "https://gitlab.com/acme/widgets/-/merge_requests/7"

DIFF = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,5 @@\n"
    " ctx\n"
    "-old\n"
    "+new = eval('x')\n"
    "+other = 1\n"
    " ctx2\n"
)


def _finding(**overrides) -> Finding:
    base = dict(
        finding_id="abc123",
        task_id="t1",
        file="app.py",
        line=2,
        title="使用 eval() 执行动态表达式",
        description="eval 有任意代码执行风险",
        trigger="expr 来自外部输入",
        evidence="new = eval('x')",
        suggestion="改用 ast.literal_eval",
        severity=Severity.critical,
        confidence=Confidence.high,
    )
    base.update(overrides)
    return Finding(**base)


def _publish(publisher, findings, head_sha="hhhhh2222", url=PR_URL):
    return publisher.publish(
        task_id="t1",
        report_path="runs/t1/report.md",
        findings=findings,
        input_ref=url,
        base_sha="bbbbb1111",
        head_sha=head_sha,
        diff_files=parse_unified_diff(DIFF),
    )


def _gh_state(comments=None, head_sha="hhhhh2222", post_fail=None):
    state = {"posts": [], "comments": comments or []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["posts"].append(json.loads(request.read().decode("utf-8")))
            if post_fail:
                return httpx.Response(post_fail, json={"message": "nope"})
            state["comments"].append({"body": state["posts"][-1]["body"]})
            return httpx.Response(201, json={"id": len(state["posts"])})
        if "pulls/42/comments" in request.url.path and request.method == "GET":
            return httpx.Response(200, json=state["comments"])
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(
                200,
                json={"head": {"sha": head_sha}, "base": {"sha": "bbbbb1111"}},
            )
        return httpx.Response(404)

    return state, handler


def _publisher(cls, handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return cls(client=client, token="tok", **kwargs)


def test_github_publishes_line_comments_with_anchor():
    state, handler = _gh_state()
    publisher = _publisher(GitHubReviewPublisher, handler)
    receipt = _publish(publisher, [_finding()])
    assert receipt.published is True
    assert receipt.posted_comments == 1
    assert len(state["posts"]) == 1
    body = state["posts"][0]
    assert body["path"] == "app.py"
    assert body["line"] == 2
    assert body["side"] == "RIGHT"
    assert body["commit_id"] == "hhhhh2222"
    assert "<!-- CRA-FINDING:abc123 -->" in body["body"]
    assert "使用 eval()" in body["body"]
    assert "高（可直接采纳）" in body["body"]


def test_github_second_publish_is_idempotent():
    state, handler = _gh_state()
    publisher = _publisher(GitHubReviewPublisher, handler)
    _publish(publisher, [_finding()])
    # 模拟"远端已有锚点"（即使本地记账丢失）：再次发布不再重复
    receipt = _publish(publisher, [_finding()])
    assert len(state["posts"]) == 1
    assert receipt.posted_comments == 0
    assert receipt.skipped_comments == 1


def test_github_head_sha_mismatch_refuses():
    state, handler = _gh_state(head_sha="newer3333")
    publisher = _publisher(GitHubReviewPublisher, handler)
    receipt = _publish(publisher, [_finding()])
    assert receipt.published is False and receipt.failed is True
    assert "拒绝发布" in receipt.detail
    assert state["posts"] == []


def test_github_unlocatable_finding_stays_in_report_only():
    state, handler = _gh_state()
    publisher = _publisher(GitHubReviewPublisher, handler)
    receipt = _publish(publisher, [_finding(line=None), _finding(finding_id="ctx9", line=1)])
    assert state["posts"] == []
    assert receipt.posted_comments == 0
    assert receipt.skipped_comments == 2
    assert "仅保留在报告" in receipt.detail


def test_github_rate_limit_yields_failed_receipt():
    state, handler = _gh_state(post_fail=429)
    publisher = _publisher(GitHubReviewPublisher, handler)
    receipt = _publish(publisher, [_finding()])
    assert receipt.failed is True
    assert "429" in receipt.detail


def test_github_comment_is_redacted():
    state, handler = _gh_state()
    publisher = _publisher(GitHubReviewPublisher, handler)
    f = _finding(evidence='token = "super-secret-value-1"')
    receipt = _publish(publisher, [f])
    assert receipt.published is True
    posted = state["posts"][0]["body"]
    assert "super-secret-value-1" not in posted


def test_gitlab_publishes_position_comments():
    state = {"posts": [], "discussions": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/discussions"):
            state["posts"].append(json.loads(request.read().decode("utf-8")))
            body = state["posts"][-1]["body"]
            state["discussions"].append({"notes": [{"body": body}]})
            return httpx.Response(201, json={"id": len(state["posts"])})
        if request.method == "GET" and request.url.path.endswith("/discussions"):
            return httpx.Response(200, json=state["discussions"])
        if request.url.path.endswith("/merge_requests/7"):
            return httpx.Response(
                200,
                json={"diff_refs": {"base_sha": "bbbbb1111", "start_sha": "bbbbb1111", "head_sha": "hhhhh2222"}},
            )
        return httpx.Response(404)

    publisher = _publisher(GitLabMRPublisher, handler)
    receipt = _publish(publisher, [_finding()], url=MR_URL)
    assert receipt.published is True
    payload = state["posts"][0]
    assert payload["position"]["new_path"] == "app.py"
    assert payload["position"]["new_line"] == 2
    assert payload["position"]["position_type"] == "text"
    assert payload["position"]["head_sha"] == "hhhhh2222"
    assert "<!-- CRA-FINDING:abc123 -->" in payload["body"]

    receipt2 = _publish(publisher, [_finding()], url=MR_URL)
    assert receipt2.posted_comments == 0 and receipt2.skipped_comments == 1


def test_gitlab_head_sha_mismatch_refuses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/merge_requests/7"):
            return httpx.Response(
                200, json={"diff_refs": {"base_sha": "b", "start_sha": "b", "head_sha": "newer3333"}}
            )
        return httpx.Response(404)

    publisher = _publisher(GitLabMRPublisher, handler)
    receipt = _publish(publisher, [_finding()], url=MR_URL)
    assert receipt.failed is True and "拒绝发布" in receipt.detail


def test_build_publisher_gates_on_explicit_flag():
    assert isinstance(build_review_publisher("github", publish=False), DryRunPublisher)
    assert isinstance(build_review_publisher("github", publish=True), GitHubReviewPublisher)
    assert isinstance(build_review_publisher("gitlab", publish=True), GitLabMRPublisher)
    with pytest.raises(ValueError):
        build_review_publisher("local", publish=True)
