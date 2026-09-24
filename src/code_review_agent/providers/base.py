from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel


class ReviewInput(BaseModel):
    source: Literal["local", "github", "gitlab"]
    raw_diff: str
    fingerprint: str
    base_sha: str | None = None
    head_sha: str | None = None
    title: str | None = None
    description: str | None = None  # PR/MR 描述，视为不可信数据


class RepositoryProvider(Protocol):
    def fetch(self, ref: str) -> ReviewInput: ...
