from __future__ import annotations

from .base import PublishReceipt, ReviewPublisher
from .dry_run import DryRunPublisher
from .gitlab import GitLabMRPublisher
from .github import GitHubReviewPublisher


def build_review_publisher(source: str, publish: bool) -> ReviewPublisher:
    """--publish 显式授权时才构造远程发布器；否则一律 dry-run。"""
    if not publish:
        return DryRunPublisher()
    if source == "github":
        return GitHubReviewPublisher()
    if source == "gitlab":
        return GitLabMRPublisher()
    raise ValueError(f"--publish 不支持来源 '{source}'（本地 diff 无发布目标；请使用 GitHub PR / GitLab MR 链接）")


__all__ = [
    "PublishReceipt",
    "ReviewPublisher",
    "DryRunPublisher",
    "GitHubReviewPublisher",
    "GitLabMRPublisher",
    "build_review_publisher",
]
