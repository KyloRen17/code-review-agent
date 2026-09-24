from __future__ import annotations

import httpx

from code_review_agent.agent.graph import build_review_graph
from code_review_agent.agent.nodes import ReviewPipeline
from code_review_agent.config import load_settings
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.providers.github import GitHubPRProvider
from code_review_agent.publishers.dry_run import DryRunPublisher
from code_review_agent.tools.dispatcher import build_dispatcher
from pathlib import Path

PR_URL = "https://github.com/acme/widgets/pull/42"

PR_META = {
    "title": "Add auth module",
    "base": {"sha": "bbbbb1111"},
    "head": {"sha": "hhhhh2222"},
}

DIFF_TEXT = (
    "diff --git a/app/auth.py b/app/auth.py\n"
    "--- /dev/null\n"
    "+++ b/app/auth.py\n"
    "@@ -0,0 +1,4 @@\n"
    "+import os\n"
    '+DB_PASSWORD = "super-secret-pass-123"\n'
    "+def run(expr):\n"
    "+    return eval(expr)\n"
)


def _handler(request: httpx.Request) -> httpx.Response:
    if "github.diff" in request.headers.get("accept", ""):
        return httpx.Response(200, text=DIFF_TEXT)
    return httpx.Response(200, json=PR_META)


def test_full_pipeline_with_github_mock_provider(tmp_path, agent_config_path, tools_config_path, session_factory):
    settings = load_settings(agent_config_path)
    settings.storage.report_dir = str(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(_handler))
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=MockLLMGateway(),
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=GitHubPRProvider(client=client, token="t"),
        task_id="ghmock1",
        session_factory=session_factory,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "ghmock1", "input_ref": PR_URL, "source": "github"},
        config={"recursion_limit": 100},
    )

    assert final["source"] == "github"
    assert final["base_sha"] == "bbbbb1111"
    assert final["head_sha"] == "hhhhh2222"
    findings = final["validated_findings"]
    assert any("eval" in f.title for f in findings)
    report = Path(final["report_path"]).read_text(encoding="utf-8")
    assert "变更区间" in report
    assert "super-secret-pass-123" not in report
    assert "[REDACTED:generic-secret]" in report
    client.close()
