from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    provider: str = "mock"
    name: str = "mock-reviewer-v1"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.2
    max_output_tokens: int = 2048


class BudgetConfig(BaseModel):
    currency: str = "CNY"
    limit: float = 10.0
    pricing_file: str = "configs/model_pricing.yaml"


class PublishingConfig(BaseModel):
    mode: str = "dry_run"


class StorageConfig(BaseModel):
    db: str = "runs/cra.sqlite"
    report_dir: str = "runs"

    @property
    def checkpoint_db(self) -> str:
        p = Path(self.db)
        return str(p.with_name(p.stem + ".ckpt.sqlite"))


class LimitsConfig(BaseModel):
    max_diff_bytes: int = 5 * 1024 * 1024
    max_work_unit_bytes: int = 64 * 1024


class ReviewConfig(BaseModel):
    recheck: bool = False
    recheck_max: int = 5


class AgentSettings(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)


def load_settings(path: Path | str | None = None) -> AgentSettings:
    if path is None:
        return AgentSettings()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return AgentSettings.model_validate(data)
