from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    source: Mapped[str] = mapped_column(String(16))
    input_ref: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    head_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    report_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class WorkUnitRecord(Base):
    __tablename__ = "work_units"
    __table_args__ = (UniqueConstraint("task_id", "unit_id", name="uq_task_unit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    unit_id: Mapped[str] = mapped_column(Text)
    file: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class FindingRecord(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("task_id", "finding_id", name="uq_task_finding"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    finding_id: Mapped[str] = mapped_column(String(64))
    file: Mapped[str] = mapped_column(Text)
    line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    trigger: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class LLMCallRecord(Base):
    __tablename__ = "llm_calls"

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    unit_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PublicationRecord(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("task_id", "mode", name="uq_task_publication"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    receipt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
