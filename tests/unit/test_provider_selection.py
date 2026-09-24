from __future__ import annotations

import io

import pytest

from code_review_agent.config import AgentSettings
from code_review_agent.providers import detect_source
from code_review_agent.providers.base import ProviderError
from code_review_agent.providers.gitlab import GitLabMRProvider
from code_review_agent.providers.github import GitHubPRProvider
from code_review_agent.providers.local import LocalDiffProvider


def test_detect_source():
    assert detect_source("https://github.com/a/b/pull/1") == "github"
    assert detect_source("https://gitlab.com/a/b/-/merge_requests/1") == "gitlab"
    assert detect_source("https://gitlab.internal.corp/a/b/-/merge_requests/3") == "gitlab"
    assert detect_source("examples/buggy.diff") == "local"
    assert detect_source("-") == "local"


def test_build_provider_types():
    from code_review_agent.providers import build_provider

    settings = AgentSettings()
    assert isinstance(
        build_provider("https://github.com/a/b/pull/1", settings), GitHubPRProvider
    )
    assert isinstance(
        build_provider("https://gitlab.com/a/b/-/merge_requests/1", settings), GitLabMRProvider
    )
    assert isinstance(build_provider("examples/buggy.diff", settings), LocalDiffProvider)


def test_local_provider_reads_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"))
    review = LocalDiffProvider().fetch("-")
    assert review.source == "local"
    assert "+++ b/x.py" in review.raw_diff
    assert len(review.fingerprint) == 64


def test_local_provider_rejects_empty_input(tmp_path):
    empty = tmp_path / "empty.diff"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ProviderError) as excinfo:
        LocalDiffProvider().fetch(str(empty))
    assert excinfo.value.kind == "invalid_input"


def test_local_provider_rejects_oversized(tmp_path):
    big = tmp_path / "big.diff"
    big.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(ProviderError) as excinfo:
        LocalDiffProvider(max_bytes=10).fetch(str(big))
    assert excinfo.value.kind == "too_large"


def test_local_provider_accepts_patch_extension(tmp_path):
    patch = tmp_path / "change.patch"
    patch.write_text(
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        encoding="utf-8",
    )
    review = LocalDiffProvider().fetch(str(patch))
    assert "b/x.py" in review.raw_diff
