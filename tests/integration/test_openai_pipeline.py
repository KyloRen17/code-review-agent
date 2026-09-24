from __future__ import annotations

import json
from pathlib import Path

import httpx

from code_review_agent.agent.graph import build_review_graph
from code_review_agent.agent.nodes import ReviewPipeline
from code_review_agent.agent.work_units import WorkUnitStatus
from code_review_agent.config import load_settings
from code_review_agent.llm.openai_compat import OpenAICompatGateway
from code_review_agent.providers.local import LocalDiffProvider
from code_review_agent.publishers.dry_run import DryRunPublisher
from code_review_agent.tools.dispatcher import build_dispatcher

FINDINGS_JSON = {
    "findings": [
        {
            "file": "app/auth.py",
            "line": 3,
            "title": "硬编码令牌",
            "severity": "high",
            "confidence": "high",
            "evidence": 'API_TOKEN = "[REDACTED:generic-secret]"',
            "description": "令牌硬编码在源码中。",
            "trigger": "仓库泄露即触发。",
            "suggestion": "改用环境变量。",
        }
    ]
}


def _settings(tmp_path, agent_config_path):
    settings = load_settings(agent_config_path)
    settings.storage.report_dir = str(tmp_path)
    return settings


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_full_pipeline_with_openai_mock_server(tmp_path, agent_config_path, tools_config_path, buggy_diff_path, session_factory):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(FINDINGS_JSON, ensure_ascii=False)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    settings = _settings(tmp_path, agent_config_path)
    gateway = OpenAICompatGateway(
        model_name="gpt-test", api_key="sk-test", client=_client(handler), base_url="https://llm.test/v1"
    )
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=gateway,
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=_local_provider(buggy_diff_path),
        task_id="oaimock1",
        session_factory=session_factory,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "oaimock1", "input_ref": str(buggy_diff_path), "source": "local"},
        config={"recursion_limit": 100},
    )

    assert len(calls) == 2  # 两个工作单元各一次调用
    assert final["usage_total"]["calls"] == 2
    assert final["usage_total"]["input_tokens"] == 200
    findings = final["validated_findings"]
    assert findings and findings[0].title == "硬编码令牌"
    assert findings[0].trigger == "仓库泄露即触发。"
    assert findings[0].origin  # LLM call_id 已挂到 finding
    assert final["report_path"]


def test_invalid_llm_output_fails_unit_but_not_pipeline(tmp_path, agent_config_path, tools_config_path, buggy_diff_path, session_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "这不是 JSON"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    settings = _settings(tmp_path, agent_config_path)
    gateway = OpenAICompatGateway(
        model_name="gpt-test", api_key="sk-test", client=_client(handler), base_url="https://llm.test/v1"
    )
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=gateway,
        dispatcher=build_dispatcher(tools_config_path),
        publisher=DryRunPublisher(),
        provider=_local_provider(buggy_diff_path),
        task_id="oaimock2",
        session_factory=session_factory,
    )
    final = build_review_graph(pipeline).invoke(
        {"task_id": "oaimock2", "input_ref": str(buggy_diff_path), "source": "local"},
        config={"recursion_limit": 100},
    )

    assert final["validated_findings"] == []
    assert len(final["errors"]) == 2
    assert all("Schema" in e for e in final["errors"])
    assert all(u.status == WorkUnitStatus.failed for u in final["work_units"])
    report = Path(final["report_path"]).read_text(encoding="utf-8")
    assert "## 错误" in report
    assert "未发现可确认问题" in report


def _local_provider(diff_path):
    return LocalDiffProvider()
