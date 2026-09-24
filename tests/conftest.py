from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
CONFIGS = REPO_ROOT / "configs"


@pytest.fixture
def buggy_diff() -> str:
    return (EXAMPLES / "buggy.diff").read_text(encoding="utf-8")


@pytest.fixture
def buggy_diff_path() -> Path:
    return EXAMPLES / "buggy.diff"


@pytest.fixture
def clean_diff() -> str:
    return (EXAMPLES / "clean.diff").read_text(encoding="utf-8")


@pytest.fixture
def clean_diff_path() -> Path:
    return EXAMPLES / "clean.diff"


@pytest.fixture
def syntax_diff_path() -> Path:
    return EXAMPLES / "syntax_error.diff"


@pytest.fixture
def agent_config_path() -> Path:
    return CONFIGS / "agent.yaml"


@pytest.fixture
def tools_config_path() -> Path:
    return CONFIGS / "tools.yaml"
