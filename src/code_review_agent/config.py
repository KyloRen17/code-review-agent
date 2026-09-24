from __future__ import annotations

import os
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


def load_env_file(path: str | Path | None = None) -> int:
    """从 .env 文件加载环境变量（本地开发便利，不引入 dotenv 依赖）。

    约束（与安全设计一致）：
    - 仅解析 KEY=VALUE 行（支持 export 前缀、# 注释、成对单/双引号）；
    - 不覆盖已存在的真实环境变量（显式设置永远优先）；
    - 值只进入 os.environ，绝不写入日志、报告或数据库（返回值仅为计数）；
    - 文件不存在或行格式非法时静默跳过，返回实际加载的变量个数。
    """
    p = Path(path) if path is not None else Path(".env")
    if not p.is_file():
        return 0
    loaded = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or not value:
            continue
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
