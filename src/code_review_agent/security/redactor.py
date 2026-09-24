from __future__ import annotations

import re

from pydantic import BaseModel

_SECRET_NAME = (
    r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[a-z0-9_]*)\b"
)

# 带引号的值：引号内的任意内容（含 base64 padding）都视为潜在 secret。
_PATTERN_QUOTED = re.compile(
    _SECRET_NAME + r"(\s*[:=]\s*)([\"'])([^\"'\n]{8,}?)\3(?=[\s,;]|$)"
)
# 裸值：值中不得含括号/等号等，且必须终止于语句边界，
# 避免 `token = issue_token(username)` 这类函数调用被误掩码导致代码损坏。
_PATTERN_BARE = re.compile(
    _SECRET_NAME + r"(\s*[:=]\s*)([^\s\"'`=;(){,]{8,})(?=[\s,;]|$)"
)

_OTHER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


class RedactionReport(BaseModel):
    matches: int = 0
    by_kind: dict[str, int] = {}


def redact(text: str) -> tuple[str, RedactionReport]:
    report = RedactionReport()

    def counted(kind: str, replacement: str):
        def repl(m: re.Match[str]) -> str:
            report.matches += 1
            report.by_kind[kind] = report.by_kind.get(kind, 0) + 1
            return replacement.format(*m.groups())

        return repl

    text = _PATTERN_QUOTED.sub(counted("generic-secret", "{0}{1}{2}[REDACTED:generic-secret]{2}"), text)
    text = _PATTERN_BARE.sub(counted("generic-secret", "{0}{1}[REDACTED:generic-secret]"), text)
    for kind, pattern in _OTHER_PATTERNS:
        text = pattern.sub(counted(kind, f"[REDACTED:{kind}]"), text)
    return text, report
