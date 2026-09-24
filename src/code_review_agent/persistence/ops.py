from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import FindingRecord, LLMCallRecord, PublicationRecord, WorkUnitRecord
from ..review.finding import Confidence, Finding, Severity


def save_work_unit(session: Session, *, task_id: str, unit_id: str, file: str, status: str, fingerprint: str | None = None, error: str | None = None) -> None:
    record = session.scalar(
        select(WorkUnitRecord).where(
            WorkUnitRecord.task_id == task_id, WorkUnitRecord.unit_id == unit_id
        )
    )
    if record is None:
        record = WorkUnitRecord(task_id=task_id, unit_id=unit_id, file=file)
        session.add(record)
    record.status = status
    record.fingerprint = fingerprint
    record.error = error
    session.commit()


def load_work_units(session: Session, task_id: str) -> dict[str, WorkUnitRecord]:
    records = session.scalars(
        select(WorkUnitRecord).where(WorkUnitRecord.task_id == task_id)
    ).all()
    return {r.unit_id: r for r in records}


def save_finding(session: Session, finding: Finding) -> None:
    record = session.scalar(
        select(FindingRecord).where(
            FindingRecord.task_id == finding.task_id,
            FindingRecord.finding_id == finding.finding_id,
        )
    )
    if record is None:
        record = FindingRecord(task_id=finding.task_id, finding_id=finding.finding_id)
        session.add(record)
    record.file = finding.file
    record.line = finding.line
    record.title = finding.title
    record.severity = finding.severity.value
    record.confidence = finding.confidence.value
    record.description = finding.description
    record.trigger = finding.trigger
    record.evidence = finding.evidence
    record.suggestion = finding.suggestion
    record.origin = finding.origin
    session.commit()


def load_findings(session: Session, task_id: str) -> list[Finding]:
    records = session.scalars(
        select(FindingRecord)
        .where(FindingRecord.task_id == task_id)
        .order_by(FindingRecord.id)
    ).all()
    return [
        Finding(
            finding_id=r.finding_id,
            task_id=r.task_id,
            file=r.file,
            line=r.line,
            title=r.title,
            description=r.description,
            trigger=r.trigger,
            evidence=r.evidence,
            suggestion=r.suggestion,
            severity=Severity(r.severity),
            confidence=Confidence(r.confidence),
            origin=r.origin or "",
        )
        for r in records
    ]


def save_llm_call(session: Session, *, call_id: str, task_id: str, unit_id: str | None, model: str, prompt_version: str | None, input_tokens: int, output_tokens: int, duration_ms: int) -> None:
    record = session.get(LLMCallRecord, call_id)
    if record is not None:
        return  # 幂等：同一 call_id 不重复入账
    session.add(
        LLMCallRecord(
            call_id=call_id,
            task_id=task_id,
            unit_id=unit_id,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
        )
    )
    session.commit()


def load_usage(session: Session, task_id: str) -> dict:
    row = session.execute(
        select(
            func.count(LLMCallRecord.call_id),
            func.coalesce(func.sum(LLMCallRecord.input_tokens), 0),
            func.coalesce(func.sum(LLMCallRecord.output_tokens), 0),
        ).where(LLMCallRecord.task_id == task_id)
    ).one()
    return {"calls": int(row[0]), "input_tokens": int(row[1]), "output_tokens": int(row[2])}


def get_publication(session: Session, task_id: str, mode: str) -> PublicationRecord | None:
    return session.scalar(
        select(PublicationRecord).where(
            PublicationRecord.task_id == task_id, PublicationRecord.mode == mode
        )
    )


def save_publication(session: Session, *, task_id: str, mode: str, idempotency_key: str, status: str, detail: str, receipt: str) -> None:
    record = get_publication(session, task_id, mode)
    if record is None:
        record = PublicationRecord(task_id=task_id, mode=mode)
        session.add(record)
    record.idempotency_key = idempotency_key
    record.status = status
    record.detail = detail
    record.receipt = receipt
    session.commit()
