from __future__ import annotations

import threading

import pytest

from code_review_agent.budget import (
    BudgetController,
    BudgetError,
    BudgetLedger,
    ModelPrice,
    PricingTable,
)


def _pricing(**prices) -> PricingTable:
    return PricingTable(
        {"mock-reviewer-v1": ModelPrice(**prices), "expensive": ModelPrice(input_per_1k=100.0, output_per_1k=100.0)},
        "CNY",
    )


def test_pricing_table_load_and_unknown_model(tmp_path):
    yaml = tmp_path / "pricing.yaml"
    yaml.write_text(
        "currency: CNY\nmodels:\n  m1:\n    input_per_1k: 1.5\n    output_per_1k: 6.0\n",
        encoding="utf-8",
    )
    table = PricingTable.load(yaml)
    assert table.currency == "CNY"
    price = table.get("m1")
    assert price.input_per_1k == 1.5 and price.output_per_1k == 6.0
    with pytest.raises(BudgetError):
        table.get("unknown-model")
    assert PricingTable.load(tmp_path / "missing.yaml") is None


def test_ledger_reserve_settle_release_lifecycle(session_factory):
    ledger = BudgetLedger(session_factory, limit=10.0, currency="CNY")
    assert ledger.reserve("t", "c1", "m", 1.0) is True
    snap = ledger.snapshot("t")
    assert snap["reserved"] == 1.0 and snap["spent"] == 0.0
    ledger.settle("t", "c1", "m", 0.8, 100, 200)
    snap = ledger.snapshot("t")
    assert snap["reserved"] == 0.0 and snap["spent"] == 0.8
    assert snap["settled_calls"] == 1 and snap["remaining"] == pytest.approx(9.2)
    ledger.reserve("t", "c2", "m", 2.0)
    ledger.release("t", "c2", "m")
    snap = ledger.snapshot("t")
    assert snap["reserved"] == 0.0 and snap["spent"] == 0.8


def test_ledger_refuses_reservation_over_limit(session_factory):
    ledger = BudgetLedger(session_factory, limit=1.0, currency="CNY")
    assert ledger.reserve("t", "c1", "m", 0.7) is True
    assert ledger.reserve("t", "c2", "m", 0.4) is False  # 0.7 + 0.4 > 1.0
    ledger.settle("t", "c1", "m", 0.7, 10, 10)
    assert ledger.reserve("t", "c3", "m", 0.3) is True  # 0.7 + 0.3 <= 1.0


def test_concurrent_reservations_never_exceed_limit(session_factory):
    ledger = BudgetLedger(session_factory, limit=5.0, currency="CNY")
    granted = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        ok = ledger.reserve("t", f"c{i}", "m", 0.6)
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = ledger.snapshot("t")
    assert sum(granted) <= 8  # 5.0 / 0.6 = 8.33
    assert snap["reserved"] <= 5.0 + 1e-9
    assert sum(granted) == snap["reserved_calls"]


def test_controller_estimate_and_reserve_and_settle(session_factory):
    controller = BudgetController(
        _pricing(input_per_1k=1.0, output_per_1k=2.0),
        BudgetLedger(session_factory, limit=100.0, currency="CNY"),
    )
    amount, est_input = controller.estimate("mock-reviewer-v1", 4000, 1000)
    assert est_input == 1000
    assert amount == pytest.approx(1.0 + 2.0)  # 1000/1000*1 + 1000/1000*2

    reservation = controller.try_reserve("t", "c1", "mock-reviewer-v1", 4000, 1000)
    assert reservation is not None and reservation.amount == pytest.approx(3.0)
    actual = controller.settle("t", reservation, 900, 500)
    assert actual == pytest.approx(0.9 + 1.0)
    snap = controller.snapshot("t")
    assert snap["spent"] == pytest.approx(1.9) and snap["reserved"] == 0.0


def test_controller_reserve_refused_when_over(session_factory):
    controller = BudgetController(
        _pricing(input_per_1k=1.0, output_per_1k=1.0),
        BudgetLedger(session_factory, limit=2.5, currency="CNY"),
    )
    assert controller.try_reserve("t", "c1", "mock-reviewer-v1", 4000, 2000) is None  # 估算 3.0 > 2.5


def test_controller_settle_without_usage_keeps_reservation(session_factory):
    controller = BudgetController(
        _pricing(input_per_1k=1.0, output_per_1k=1.0),
        BudgetLedger(session_factory, limit=100.0, currency="CNY"),
    )
    reservation = controller.try_reserve("t", "c1", "mock-reviewer-v1", 4000, 1000)
    assert reservation is not None
    controller.settle("t", reservation, 0, 0)  # usage 不可得
    snap = controller.snapshot("t")
    assert snap["reserved"] == pytest.approx(2.0)  # 保守保留预留额（待结算）
    assert snap["spent"] == 0.0


def test_controller_unknown_model_raises(session_factory):
    controller = BudgetController(_pricing(), BudgetLedger(session_factory, 10.0, "CNY"))
    with pytest.raises(BudgetError):
        controller.try_reserve("t", "c1", "not-in-table", 100, 100)
