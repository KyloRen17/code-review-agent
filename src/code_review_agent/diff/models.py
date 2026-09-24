from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FileChangeKind(str, Enum):
    modified = "modified"
    added = "added"
    deleted = "deleted"
    renamed = "renamed"
    binary = "binary"


class DiffLine(BaseModel):
    kind: str = "context"  # "context" | "added" | "removed"
    old_line: int | None = None
    new_line: int | None = None
    content: str = ""


class DiffHunk(BaseModel):
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = Field(default_factory=list)

    def new_line_range(self) -> tuple[int, int] | None:
        if self.new_count == 0:
            return None
        return (self.new_start, self.new_start + self.new_count - 1)

    def text(self) -> str:
        out = [self.header]
        prefix = {"added": "+", "removed": "-", "context": " "}
        for line in self.lines:
            out.append(prefix.get(line.kind, " ") + line.content)
        return "\n".join(out)


class DiffFile(BaseModel):
    old_path: str
    new_path: str
    kind: FileChangeKind = FileChangeKind.modified
    hunks: list[DiffHunk] = Field(default_factory=list)

    @property
    def path(self) -> str:
        if self.new_path not in ("/dev/null", ""):
            return self.new_path
        return self.old_path
