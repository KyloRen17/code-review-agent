from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..persistence.models import BudgetEntryRecord, BudgetSummaryRecord

_EPS = 1e-9


class BudgetLedger:
    """预算账本：journal（逐条 reserve/settle/release）+ 汇总行。

    原子性：进程内 threading.Lock + SQLite 单写者。跨进程并发未覆盖
    （当前为单 CLI 进程执行模型任务）。
    """

    def __init__(self, session_factory: sessionmaker[Session], limit: float, currency: str) -> None:
        self._sf = session_factory
        self.limit = float(limit)
        self.currency = currency
        self._lock = threading.Lock()

    def _summary(self, session: Session, task_id: str) -> BudgetSummaryRecord:
        record = session.get(BudgetSummaryRecord, task_id)
        if record is None:
            record = BudgetSummaryRecord(
                task_id=task_id, currency=self.currency, limit_amount=self.limit
            )
            session.add(record)
            session.flush()
        return record

    def reserve(self, task_id: str, call_id: str, model: str, amount: float) -> bool:
        with self._lock:
            with self._sf() as session:
                summary = self._summary(session, task_id)
                if summary.spent + summary.reserved + amount > self.limit + _EPS:
                    session.rollback()
                    return False
                summary.reserved += amount
                summary.reserved_calls += 1
                session.add(
                    BudgetEntryRecord(
                        task_id=task_id, call_id=call_id, model=model, kind="reserve", amount=amount
                    )
                )
                session.commit()
                return True

    def settle(
        self,
        task_id: str,
        call_id: str,
        model: str,
        amount: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        with self._lock:
            with self._sf() as session:
                summary = self._summary(session, task_id)
                reserved_entry = session.scalar(
                    select(BudgetEntryRecord)
                    .where(
                        BudgetEntryRecord.task_id == task_id,
                        BudgetEntryRecord.call_id == call_id,
                        BudgetEntryRecord.kind == "reserve",
                    )
                    .order_by(BudgetEntryRecord.id.desc())
                )
                if reserved_entry is not None:
                    summary.reserved = max(0.0, summary.reserved - reserved_entry.amount)
                    summary.reserved_calls = max(0, summary.reserved_calls - 1)
                summary.spent += amount
                summary.settled_calls += 1
                session.add(
                    BudgetEntryRecord(
                        task_id=task_id,
                        call_id=call_id,
                        model=model,
                        kind="settle",
                        amount=amount,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                )
                session.commit()

    def release(self, task_id: str, call_id: str, model: str) -> None:
        with self._lock:
            with self._sf() as session:
                summary = self._summary(session, task_id)
                reserved_entry = session.scalar(
                    select(BudgetEntryRecord)
                    .where(
                        BudgetEntryRecord.task_id == task_id,
                        BudgetEntryRecord.call_id == call_id,
                        BudgetEntryRecord.kind == "reserve",
                    )
                    .order_by(BudgetEntryRecord.id.desc())
                )
                if reserved_entry is None:
                    session.rollback()
                    return
                summary.reserved = max(0.0, summary.reserved - reserved_entry.amount)
                summary.reserved_calls = max(0, summary.reserved_calls - 1)
                session.add(
                    BudgetEntryRecord(
                        task_id=task_id, call_id=call_id, model=model, kind="release", amount=0.0
                    )
                )
                session.commit()

    def snapshot(self, task_id: str) -> dict:
        with self._sf() as session:
            summary = session.get(BudgetSummaryRecord, task_id)
            if summary is None:
                return {
                    "currency": self.currency,
                    "limit": self.limit,
                    "spent": 0.0,
                    "reserved": 0.0,
                    "reserved_calls": 0,
                    "settled_calls": 0,
                    "remaining": self.limit,
                    "exhausted": False,
                }
            spent = float(summary.spent)
            reserved = float(summary.reserved)
            return {
                "currency": summary.currency,
                "limit": float(summary.limit_amount),
                "spent": spent,
                "reserved": reserved,
                "reserved_calls": int(summary.reserved_calls),
                "settled_calls": int(summary.settled_calls),
                "remaining": max(0.0, self.limit - spent - reserved),
                "exhausted": spent + reserved >= self.limit - _EPS,
            }
