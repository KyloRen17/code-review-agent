from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..persistence.models import SpanRecord
from ..security.redactor import redact

_current_span: ContextVar[str | None] = ContextVar("current_span", default=None)


def current_span_id() -> str | None:
    return _current_span.get()


class SpanRecorder:
    """轻量 span 记录器：trace_id = task_id，span 结构对齐常见遥测导出格式。

    存储与查询走本地 SQLite；attributes 只放安全标量（计数、名称、耗时），
    文本类内容（prompt/响应）由调用方脱敏后另行存入 llm_calls 表。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    @contextmanager
    def span(self, trace_id: str, name: str, kind: str, attributes: dict | None = None):
        span_id = uuid.uuid4().hex[:16]
        parent_span_id = _current_span.get()
        safe_attributes = {
            k: v for k, v in (attributes or {}).items() if isinstance(v, (int, float, str, bool))
        }
        with self.session_factory() as session:
            session.add(
                SpanRecord(
                    span_id=span_id,
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                    name=name,
                    kind=kind,
                    attributes=json.dumps(safe_attributes, ensure_ascii=False),
                )
            )
            session.commit()
        token = _current_span.set(span_id)
        started = datetime.now(timezone.utc)
        try:
            yield span_id
        except Exception as exc:
            self._finish(span_id, started, status="error", error=str(exc)[:2000])
            raise
        finally:
            _current_span.reset(token)
        self._finish(span_id, started, status="ok")

    def _finish(
        self, span_id: str, started: datetime, *, status: str, error: str | None = None
    ) -> None:
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)
        with self.session_factory() as session:
            record = session.get(SpanRecord, span_id)
            if record is not None:
                record.status = status
                record.finished_at = finished
                record.duration_ms = duration_ms
                if error:
                    record.error_message = redact(error)[0]
                session.commit()

    def load_spans(self, trace_id: str) -> list[SpanRecord]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(SpanRecord)
                    .where(SpanRecord.trace_id == trace_id)
                    .order_by(SpanRecord.started_at)
                ).all()
            )


__all__ = ["SpanRecorder", "current_span_id"]
