from __future__ import annotations

from code_review_agent.llm.gateway import LLMRequest
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.llm.schemas import parse_llm_findings


def _request(code: str, new_start: int, file: str = "app/auth.py") -> LLMRequest:
    return LLMRequest(
        call_id="c1",
        model="mock-reviewer-v1",
        system="system prompt",
        prompt="user prompt",
        context={"file": file, "code": code, "new_start": new_start},
    )


AUTH_HUNK = """@@ -0,0 +1,19 @@
+import subprocess
+
+API_TOKEN = "sk-live-9f8e7d6c5b4a3210"
+
+def unsafe_eval(expr):
+    return eval(expr)"""


def test_mock_returns_schema_valid_output():
    gateway = MockLLMGateway()
    resp = gateway.complete(_request(AUTH_HUNK, 1))
    parsed = parse_llm_findings(resp.content)
    assert parsed.findings, "mock 应在含已知问题的 hunk 上产出 findings"
    for f in parsed.findings:
        assert f.file == "app/auth.py"
        assert f.line is not None and f.line >= 1


def test_mock_finds_eval_and_secret_with_correct_lines():
    gateway = MockLLMGateway()
    parsed = parse_llm_findings(gateway.complete(_request(AUTH_HUNK, 1)).content)
    by_line = {f.line: f.title for f in parsed.findings}
    assert "使用 eval() 执行动态表达式" in by_line.get(6)
    assert "疑似硬编码凭证" in by_line.get(3)


def test_mock_ignores_removed_lines():
    code = "@@ -1,3 +1,1 @@\n-eval('removed')\n+safe = 1"
    parsed = parse_llm_findings(MockLLMGateway().complete(_request(code, 1)).content)
    assert parsed.findings == []


def test_mock_is_deterministic():
    gateway = MockLLMGateway()
    a = gateway.complete(_request(AUTH_HUNK, 1))
    b = gateway.complete(_request(AUTH_HUNK, 1))
    assert a.content == b.content


def test_mock_usage_is_positive():
    resp = MockLLMGateway().complete(_request(AUTH_HUNK, 1))
    assert resp.usage.input_tokens >= 1
    assert resp.usage.output_tokens >= 1
    assert resp.finish == "stop"
