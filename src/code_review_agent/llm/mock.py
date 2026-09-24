from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .gateway import LLMRequest, LLMResponse, Usage
from .schemas import LLMFinding


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    pattern: re.Pattern[str]
    title: str
    severity: str
    confidence: str
    description: str
    suggestion: str
    trigger: str | None = None


_RULES = [
    _Rule(
        rule_id="dangerous-eval",
        pattern=re.compile(r"\beval\s*\("),
        title="使用 eval() 执行动态表达式",
        severity="critical",
        confidence="high",
        description="eval 会执行任意代码，存在命令注入与数据泄露风险。",
        suggestion="改用 ast.literal_eval 或显式解析输入。",
        trigger="expr 来源于外部输入或字符串拼接。",
    ),
    _Rule(
        rule_id="subprocess-shell-true",
        pattern=re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
        title="subprocess 使用 shell=True",
        severity="critical",
        confidence="high",
        description="shell=True 与外部输入拼接时可被注入任意 shell 命令。",
        suggestion="移除 shell=True，使用参数列表形式调用。",
        trigger="cmd 参数包含外部可控内容时。",
    ),
    _Rule(
        rule_id="hardcoded-secret",
        pattern=re.compile(r"(?i)\b\w*(password|secret|token|api_?key)\w*\b\s*=\s*[\"'][^\"'\s]{6,}[\"']"),
        title="疑似硬编码凭证",
        severity="high",
        confidence="high",
        description="凭证硬编码在源码中，会随仓库历史泄露。",
        suggestion="从环境变量或密钥管理服务读取。",
        trigger="仓库被克隆或历史泄露即触发（无条件）。",
    ),
    _Rule(
        rule_id="swallowed-exception",
        pattern=re.compile(r"except\s*(Exception)?\s*:\s*pass\b"),
        title="异常被静默吞掉",
        severity="medium",
        confidence="reference",
        description="空 except/pass 会掩盖真实故障，排障困难。",
        suggestion="记录日志或至少向上抛出。",
        trigger="被捕获的代码路径抛出异常时。",
    ),
    _Rule(
        rule_id="mutable-default-arg",
        pattern=re.compile(r"def\s+\w+\([^)]*=\s*(\[\]|\{\})"),
        title="可变默认参数",
        severity="medium",
        confidence="reference",
        description="可变默认参数在多次调用间共享状态。",
        suggestion="默认值设为 None，在函数体内初始化。",
        trigger="多次调用且未显式传入该参数时。",
    ),
    _Rule(
        rule_id="none-equality",
        pattern=re.compile(r"[!=]=\s*None\b"),
        title="应使用 is/is not 比较 None",
        severity="low",
        confidence="reference",
        description="PEP 8 规定与 None 比较应使用身份运算符。",
        suggestion="改为 is None / is not None。",
        trigger="操作数重载 __eq__ 时行为可能异常。",
    ),
]


class MockLLMGateway:
    """确定性规则模拟器，不是真实模型。

    用途：在无网络、无 API Key 的环境下走通完整流水线并产出 schema 合法的
    findings，供测试与演示复现。真实模型在 Phase 3 通过 OpenAI-compatible
    网关接入，接口与 Mock 完全一致。
    """

    def __init__(self, model_name: str = "mock-reviewer-v1") -> None:
        self.name = "mock"
        self.model_name = model_name

    def complete(self, request: LLMRequest) -> LLMResponse:
        if request.context.get("purpose") == "recheck":
            return self._recheck(request)
        file = str(request.context.get("file", ""))
        new_start = int(request.context.get("new_start") or 1)
        code = str(request.context.get("code", ""))
        findings = self._scan(code, file, new_start)
        content = json.dumps(
            {"findings": [f.model_dump() for f in findings]}, ensure_ascii=False, indent=2
        )
        input_tokens = max(1, (len(request.system) + len(request.prompt)) // 4)
        output_tokens = max(1, len(content) // 4)
        return LLMResponse(
            call_id=request.call_id,
            model=self.model_name,
            content=content,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def _recheck(self, request: LLMRequest) -> LLMResponse:
        evidence = str(request.context.get("evidence", ""))
        hit = any(rule.pattern.search(evidence) for rule in _RULES)
        if hit:
            payload = {"verdict": "confirmed", "reason": "规则复核命中：证据中存在可独立检出的风险模式"}
        else:
            payload = {"verdict": "uncertain", "reason": "规则复核未找到独立证据，无法确认"}
        content = json.dumps(payload, ensure_ascii=False)
        input_tokens = max(1, (len(request.system) + len(request.prompt)) // 4)
        output_tokens = max(1, len(content) // 4)
        return LLMResponse(
            call_id=request.call_id,
            model=self.model_name,
            content=content,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def _scan(self, code: str, file: str, new_start: int) -> list[LLMFinding]:
        out: list[LLMFinding] = []
        ln = new_start
        prev_content = ""
        for raw in code.splitlines():
            if raw.startswith("+") or raw.startswith(" "):
                content = raw[1:]
                for rule in _RULES:
                    if rule.pattern.search(content):
                        out.append(
                            LLMFinding(
                                file=file,
                                line=ln,
                                title=rule.title,
                                severity=rule.severity,  # type: ignore[arg-type]
                                confidence=rule.confidence,  # type: ignore[arg-type]
                                evidence=content.strip(),
                                description=rule.description,
                                trigger=rule.trigger,
                                suggestion=rule.suggestion,
                            )
                        )
                if prev_content.lstrip().startswith("except") and content.strip() == "pass":
                    out.append(
                        LLMFinding(
                            file=file,
                            line=ln,
                            title="异常被静默吞掉",
                            severity="medium",
                            confidence="reference",
                            evidence=f"{prev_content.strip()} / pass",
                            description="空 except/pass 会掩盖真实故障，排障困难。",
                            trigger="被捕获的代码路径抛出异常时。",
                            suggestion="记录日志或至少向上抛出。",
                        )
                    )
                prev_content = content
                ln += 1
            elif raw.startswith("-"):
                continue
            else:
                if raw.startswith("@@"):
                    continue
        return out
