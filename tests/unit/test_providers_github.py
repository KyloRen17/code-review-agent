from __future__ import annotations

import httpx
import pytest

from code_review_agent.providers.base import ProviderError
from code_review_agent.providers.github import GitHubPRProvider

PR_URL = "https://github.com/acme/widgets/pull/42"
PR_META = {
    "title": "Add auth module",
    "body": "PR description (untrusted)",
    "base": {"sha": "bbbbb1111"},
    "head": {"sha": "hhhhh2222"},
}
DIFF_TEXT = (
    "diff --git a/app/auth.py b/app/auth.py\n"
    "--- a/app/auth.py\n"
    "+++ b/app/auth.py\n"
    "@@ -1,2 +1,3 @@\n"
    " import os\n"
    "-x = 1\n"
    "+x = 2\n"
    "+y = eval('1')\n"
)


def _provider(handler, **kwargs) -> GitHubPRProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubPRProvider(client=client, token="test-token", **kwargs)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if "github.diff" in request.headers.get("accept", ""):
        return httpx.Response(200, text=DIFF_TEXT)
    return httpx.Response(200, json=PR_META)


def test_fetch_returns_unified_review_input():
    provider = _provider(_ok_handler)
    review = provider.fetch(PR_URL)
    assert review.source == "github"
    assert review.raw_diff == DIFF_TEXT
    assert review.fingerprint and len(review.fingerprint) == 64
    assert review.base_sha == "bbbbb1111"
    assert review.head_sha == "hhhhh2222"
    assert review.title == "Add auth module"
    assert review.description == "PR description (untrusted)"


def test_sends_bearer_token(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=PR_META)

    provider = _provider(handler)
    try:
        provider.fetch(PR_URL)
    except ProviderError:
        pass
    assert captured["authorization"] == "Bearer test-token"


def test_404_maps_to_not_found():
    provider = _provider(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "not_found"


def test_401_maps_to_auth_failed():
    provider = _provider(lambda request: httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "auth_failed"


def test_403_with_rate_limit_zero_maps_to_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "rate limited"},
            headers={"x-ratelimit-remaining": "0"},
        )

    provider = _provider(handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "rate_limited"


def test_429_maps_to_rate_limited():
    provider = _provider(lambda request: httpx.Response(429, json={"message": "Too Many Requests"}))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "rate_limited"


def test_oversized_diff_maps_to_too_large():
    provider = _provider(_ok_handler, max_bytes=10)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "too_large"


def test_invalid_url_maps_to_invalid_input():
    provider = _provider(_ok_handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch("https://github.com/acme/widgets/issues/42")
    assert excinfo.value.kind == "invalid_input"


def test_empty_diff_maps_to_not_found():
    provider = _provider(
        lambda request: (
            httpx.Response(200, text="")
            if "github.diff" in request.headers.get("accept", "")
            else httpx.Response(200, json=PR_META)
        )
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "not_found"


def test_network_error_maps_to_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(PR_URL)
    assert excinfo.value.kind == "network"
