from __future__ import annotations

import httpx
import pytest

from code_review_agent.diff import FileChangeKind, parse_unified_diff
from code_review_agent.providers.base import ProviderError
from code_review_agent.providers.gitlab import GitLabMRProvider

MR_URL = "https://gitlab.com/acme/team/widgets/-/merge_requests/7"
PROJECT_PATH = "acme/team/widgets"

MR_META = {
    "title": "Fix auth flow",
    "description": "MR description (untrusted)",
    "diff_refs": {"base_sha": "aaaa0000", "head_sha": "bbbb1111"},
}
MR_CHANGES = {
    "changes": [
        {
            "old_path": "app/auth.py",
            "new_path": "app/auth.py",
            "diff": "@@ -1,2 +1,3 @@\n import os\n-x = 1\n+x = 2\n+y = eval('1')\n",
        },
        {
            "old_path": None,
            "new_path": "app/new.py",
            "new_file": True,
            "diff": "@@ -0,0 +1,1 @@\n+print('hi')\n",
        },
        {
            "old_path": "app/legacy.py",
            "new_path": "app/legacy.py",
            "deleted_file": True,
            "diff": "@@ -1,1 +0,0 @@\n-print('bye')\n",
        },
    ]
}


def _provider(handler, **kwargs) -> GitLabMRProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitLabMRProvider(client=client, token="test-token", **kwargs)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/changes"):
        return httpx.Response(200, json=MR_CHANGES)
    return httpx.Response(200, json=MR_META)


def test_fetch_returns_unified_review_input():
    provider = _provider(_ok_handler)
    review = provider.fetch(MR_URL)
    assert review.source == "gitlab"
    assert review.base_sha == "aaaa0000"
    assert review.head_sha == "bbbb1111"
    assert review.title == "Fix auth flow"
    files = parse_unified_diff(review.raw_diff)
    assert [f.path for f in files] == ["app/auth.py", "app/new.py", "app/legacy.py"]
    assert files[1].kind == FileChangeKind.added
    assert files[2].kind == FileChangeKind.deleted
    assert files[0].hunks[0].new_start == 1


def test_sends_private_token_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["token"] = request.headers.get("private-token")
        return httpx.Response(200, json=MR_META)

    provider = _provider(handler)
    try:
        provider.fetch(MR_URL)
    except ProviderError:
        pass
    assert captured["token"] == "test-token"


def test_nested_group_project_path_is_encoded():
    request_url = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_url["url"] = str(request.url)
        if request.url.path.endswith("/changes"):
            return httpx.Response(200, json=MR_CHANGES)
        return httpx.Response(200, json=MR_META)

    provider = _provider(handler)
    provider.fetch(MR_URL)
    assert "/projects/acme%2Fteam%2Fwidgets/merge_requests/7" in str(request_url)


def test_404_maps_to_not_found():
    provider = _provider(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(MR_URL)
    assert excinfo.value.kind == "not_found"


def test_401_maps_to_auth_failed():
    provider = _provider(lambda request: httpx.Response(401, json={"message": "401 Unauthorized"}))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(MR_URL)
    assert excinfo.value.kind == "auth_failed"


def test_429_maps_to_rate_limited():
    provider = _provider(lambda request: httpx.Response(429, json={"error": "Too Many Requests"}))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(MR_URL)
    assert excinfo.value.kind == "rate_limited"


def test_invalid_url_maps_to_invalid_input():
    provider = _provider(_ok_handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch("https://gitlab.com/acme/widgets/-/issues/7")
    assert excinfo.value.kind == "invalid_input"


def test_oversized_diff_maps_to_too_large():
    provider = _provider(_ok_handler, max_bytes=10)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(MR_URL)
    assert excinfo.value.kind == "too_large"
