from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class BudgetError(Exception):
    pass


class ModelPrice(BaseModel):
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0
    currency: str = "CNY"


class PricingTable:
    def __init__(self, prices: dict[str, ModelPrice], currency: str = "CNY") -> None:
        self._prices = prices
        self.currency = currency

    @classmethod
    def load(cls, path: str | Path | None) -> "PricingTable | None":
        if path is None:
            return None
        p = Path(path)
        if not p.is_file():
            return None
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        currency = str(data.get("currency", "CNY"))
        prices = {}
        for model, spec in (data.get("models") or {}).items():
            spec = spec or {}
            prices[model] = ModelPrice(
                input_per_1k=float(spec.get("input_per_1k", 0.0)),
                output_per_1k=float(spec.get("output_per_1k", 0.0)),
                currency=str(spec.get("currency", currency)),
            )
        return cls(prices, currency)

    def get(self, model: str) -> ModelPrice:
        price = self._prices.get(model)
        if price is None:
            raise BudgetError(
                f"模型 '{model}' 未在单价表中登记；为避免不可控成本，拒绝调用（请在 model_pricing.yaml 补充单价）"
            )
        return price

    def has(self, model: str) -> bool:
        return model in self._prices
