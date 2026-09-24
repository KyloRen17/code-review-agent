from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

_ALLOWED_MODULES = [
    ("code_review_agent.review.finding", "Finding"),
    ("code_review_agent.review.finding", "Severity"),
    ("code_review_agent.review.finding", "Confidence"),
    ("code_review_agent.agent.work_units", "WorkUnit"),
    ("code_review_agent.agent.work_units", "WorkUnitStatus"),
    ("code_review_agent.diff.models", "DiffFile"),
    ("code_review_agent.diff.models", "DiffHunk"),
    ("code_review_agent.diff.models", "DiffLine"),
    ("code_review_agent.diff.models", "FileChangeKind"),
    ("code_review_agent.security.redactor", "RedactionReport"),
    ("code_review_agent.tools.base", "ToolResult"),
    ("code_review_agent.tools.base", "ToolStatus"),
]


def create_checkpointer(path: str | Path) -> SqliteSaver:
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    saver = SqliteSaver(
        conn,
        serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODULES),
    )
    saver.setup()
    return saver


def has_checkpoint(saver: SqliteSaver, thread_id: str) -> bool:
    return next(saver.list({"configurable": {"thread_id": thread_id}}), None) is not None


__all__ = ["create_checkpointer", "has_checkpoint"]
