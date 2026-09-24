from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

SourceKind = Literal["local", "github", "gitlab"]

ErrorKind = Literal[
    "not_found",
    "auth_failed",
    "rate_limited",
    "too_large",
    "network",
    "invalid_input",
    "http",
]


class ProviderError(Exception):
    """带明确状态码的输入获取失败，CLI 会将其写入任务记录并终止。"""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(f"[{kind}] {message}")
        self.kind: ErrorKind = kind


class ReviewInput(BaseModel):
    source: SourceKind
    raw_diff: str
    fingerprint: str
    base_sha: str | None = None
    head_sha: str | None = None
    title: str | None = None
    description: str | None = None  # PR/MR 描述，视为不可信数据，不进入 prompt


class RepositoryProvider(Protocol):
    source: SourceKind

    def fetch(self, ref: str) -> ReviewInput: ...
