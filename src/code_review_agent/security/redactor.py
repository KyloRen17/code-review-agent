from __future__ import annotations

import re

from pydantic import BaseModel

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "aws-access-key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    (
        "generic-secret",
        re.compile(
            r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[a-z0-9_]*)\b"
            r"(\s*[:=]\s*)([\"']?)([^\s\"',;)]{8,})\3"
        ),
    ),
]


class RedactionReport(BaseModel):
    matches: int = 0
    by_kind: dict[str, int] = {}


def redact(text: str) -> tuple[str, RedactionReport]:
    report = RedactionReport()

    def make_repl(kind: str):
        def repl(m: re.Match[str]) -> str:
            report.matches += 1
            report.by_kind[kind] = report.by_kind.get(kind, 0) + 1
            if kind == "generic-secret":
                return f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED:generic-secret]{m.group(3)}"
            return f"[REDACTED:{kind}]"

        return repl

    for kind, pattern in _PATTERNS:
        text = pattern.sub(make_repl(kind), text)
    return text, report
