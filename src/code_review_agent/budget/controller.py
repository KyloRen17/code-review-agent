from __future__ import annotations

from dataclasses import dataclass

from .ledger import BudgetLedger
from .pricing import BudgetError, ModelPrice, PricingTable

_CHARS_PER_TOKEN = 4  # 估算口径：约 4 字符 = 1 token，仅用于调用前预留


@dataclass
class Reservation:
    call_id: str
    model: str
    amount: float
    estimated_input_tokens: int


class BudgetController:
    """调用前预留 → 调用后结算的预算闸门。

    决策完全由确定性代码执行，LLM 与工具无权跳过。
    """

    def __init__(self, pricing: PricingTable, ledger: BudgetLedger) -> None:
        self.pricing = pricing
        self.ledger = ledger

    @property
    def limit(self) -> float:
        return self.ledger.limit

    @property
    def currency(self) -> str:
        return self.ledger.currency

    def _price(self, model: str) -> ModelPrice:
        return self.pricing.get(model)

    def estimate(self, model: str, prompt_chars: int, max_output_tokens: int) -> tuple[float, int]:
        price = self._price(model)
        est_input = prompt_chars // _CHARS_PER_TOKEN
        amount = est_input / 1000 * price.input_per_1k + max_output_tokens / 1000 * price.output_per_1k
        return round(amount, 6), est_input

    def try_reserve(
        self, task_id: str, call_id: str, model: str, prompt_chars: int, max_output_tokens: int
    ) -> Reservation | None:
        """预留失败（预算不足）返回 None，调用方不得发起付费调用。"""
        amount, est_input = self.estimate(model, prompt_chars, max_output_tokens)
        if not self.ledger.reserve(task_id, call_id, model, amount):
            return None
        return Reservation(call_id=call_id, model=model, amount=amount, estimated_input_tokens=est_input)

    def settle(
        self, task_id: str, reservation: Reservation, input_tokens: int, output_tokens: int
    ) -> float:
        """按服务商返回的实际 usage 结算；返回实际金额。

        usage 不可得（input/output 均为 0）时保守保留预留额，不结算——
        该部分在 snapshot 中以 reserved（待结算）呈现，直到人工核对或明确策略处理。
        """
        if input_tokens <= 0 and output_tokens <= 0:
            return 0.0
        price = self._price(reservation.model)
        amount = round(
            input_tokens / 1000 * price.input_per_1k + output_tokens / 1000 * price.output_per_1k, 6
        )
        self.ledger.settle(
            task_id,
            reservation.call_id,
            reservation.model,
            amount,
            input_tokens,
            output_tokens,
        )
        return amount

    def release(self, task_id: str, reservation: Reservation) -> None:
        self.ledger.release(task_id, reservation.call_id, reservation.model)

    def snapshot(self, task_id: str) -> dict:
        return self.ledger.snapshot(task_id)
