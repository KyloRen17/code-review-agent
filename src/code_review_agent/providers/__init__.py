from __future__ import annotations

import re

from ..config import AgentSettings
from .base import ProviderError, RepositoryProvider, ReviewInput, SourceKind
from .gitlab import GitLabMRProvider
from .github import GitHubPRProvider
from .local import LocalDiffProvider

_GITHUB_URL_RE = re.compile(r"^https?://([^/]*\.)?github\.com/", re.IGNORECASE)
_GITLAB_URL_RE = re.compile(r"^https?://([^/]*\.)?gitlab[\w.\-]*/", re.IGNORECASE)


def detect_source(ref: str) -> SourceKind:
    stripped = ref.strip()
    if _GITHUB_URL_RE.match(stripped):
        return "github"
    if _GITLAB_URL_RE.match(stripped):
        return "gitlab"
    return "local"


def build_provider(ref: str, settings: AgentSettings) -> RepositoryProvider:
    source = detect_source(ref)
    max_bytes = settings.limits.max_diff_bytes
    if source == "github":
        return GitHubPRProvider(max_bytes=max_bytes)
    if source == "gitlab":
        return GitLabMRProvider(max_bytes=max_bytes)
    return LocalDiffProvider(max_bytes=max_bytes)


__all__ = [
    "ProviderError",
    "RepositoryProvider",
    "ReviewInput",
    "LocalDiffProvider",
    "GitHubPRProvider",
    "GitLabMRProvider",
    "detect_source",
    "build_provider",
]
