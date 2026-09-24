from __future__ import annotations

import re

from .models import DiffFile, DiffHunk, DiffLine, FileChangeKind

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_BINARY_RE = re.compile(r"^Binary files .+ differ$|^GIT binary patch")
_META_PREFIXES = (
    "index ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "new file mode ",
    "deleted file mode ",
)


def _clean_path(raw: str) -> str:
    p = raw.split("\t")[0].strip()
    if p.startswith(("a/", "b/")) and p != "/dev/null":
        return p[2:]
    return p


def parse_unified_diff(text: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    cur: DiffFile | None = None
    hunk: DiffHunk | None = None
    old_ln = 0
    new_ln = 0

    def flush_file() -> None:
        nonlocal cur, hunk
        if cur is not None:
            if cur.kind == FileChangeKind.modified:
                if cur.old_path == "/dev/null":
                    cur.kind = FileChangeKind.added
                elif cur.new_path == "/dev/null":
                    cur.kind = FileChangeKind.deleted
            files.append(cur)
        cur = None
        hunk = None

    for raw_line in text.splitlines():
        if raw_line.startswith("diff --git "):
            flush_file()
            m = _FILE_RE.match(raw_line)
            if m:
                cur = DiffFile(old_path=m.group(1), new_path=m.group(2))
            else:
                cur = DiffFile(old_path="", new_path="")
            continue
        if cur is None:
            continue
        if hunk is None:
            if raw_line.startswith("--- "):
                cur.old_path = _clean_path(raw_line[4:])
                continue
            if raw_line.startswith("+++ "):
                cur.new_path = _clean_path(raw_line[4:])
                continue
            if _BINARY_RE.match(raw_line):
                cur.kind = FileChangeKind.binary
                continue
            if raw_line.startswith("rename from "):
                cur.old_path = raw_line[12:]
                cur.kind = FileChangeKind.renamed
                continue
            if raw_line.startswith("rename to "):
                cur.new_path = raw_line[10:]
                cur.kind = FileChangeKind.renamed
                continue
            if raw_line.startswith(_META_PREFIXES):
                if raw_line.startswith("new file mode"):
                    cur.kind = FileChangeKind.added
                elif raw_line.startswith("deleted file mode"):
                    cur.kind = FileChangeKind.deleted
                continue
        m = _HUNK_RE.match(raw_line)
        if m and hunk is None:
            hunk = DiffHunk(
                header=raw_line,
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or 1),
            )
            cur.hunks.append(hunk)
            old_ln, new_ln = hunk.old_start, hunk.new_start
            continue
        if hunk is not None:
            if raw_line.startswith("+"):
                hunk.lines.append(DiffLine(kind="added", new_line=new_ln, content=raw_line[1:]))
                new_ln += 1
            elif raw_line.startswith("-"):
                hunk.lines.append(DiffLine(kind="removed", old_line=old_ln, content=raw_line[1:]))
                old_ln += 1
            elif raw_line.startswith(" "):
                hunk.lines.append(
                    DiffLine(kind="context", old_line=old_ln, new_line=new_ln, content=raw_line[1:])
                )
                old_ln += 1
                new_ln += 1
            elif raw_line == "\\ No newline at end of file":
                continue
            else:
                hunk = None
    flush_file()
    return files
