from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from .base import ProviderError, ReviewInput


class LocalDiffProvider:
    source = "local"

    def __init__(self, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes

    def fetch(self, ref: str) -> ReviewInput:
        if ref == "-":
            raw = sys.stdin.read()
            size = len(raw.encode("utf-8"))
        else:
            path = Path(ref)
            if not path.is_file():
                raise ProviderError("not_found", f"diff 文件不存在: {ref}")
            size = path.stat().st_size
            if size > self.max_bytes:
                raise ProviderError(
                    "too_large", f"diff 大小 {size} 字节超过上限 {self.max_bytes} 字节，拒绝处理"
                )
            raw = path.read_text(encoding="utf-8", errors="replace")
        if size > self.max_bytes:
            raise ProviderError(
                "too_large", f"diff 大小 {size} 字节超过上限 {self.max_bytes} 字节，拒绝处理"
            )
        if not raw.strip():
            raise ProviderError("invalid_input", f"输入为空: {ref}")
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return ReviewInput(source="local", raw_diff=raw, fingerprint=fingerprint)
