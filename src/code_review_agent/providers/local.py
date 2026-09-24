from __future__ import annotations

import hashlib
from pathlib import Path

from .base import ReviewInput


class LocalDiffProvider:
    def __init__(self, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes

    def fetch(self, ref: str) -> ReviewInput:
        path = Path(ref)
        if not path.is_file():
            raise FileNotFoundError(f"diff 文件不存在: {ref}")
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"diff 大小 {size} 字节超过上限 {self.max_bytes} 字节，拒绝处理")
        raw = path.read_text(encoding="utf-8", errors="replace")
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return ReviewInput(source="local", raw_diff=raw, fingerprint=fingerprint)
