from __future__ import annotations

from code_review_agent.config import load_settings


def test_default_settings_without_file():
    settings = load_settings(None)
    assert settings.model.provider == "mock"
    assert settings.budget.limit == 10.0
    assert settings.publishing.mode == "dry_run"


def test_load_settings_from_yaml(agent_config_path):
    settings = load_settings(agent_config_path)
    assert settings.model.provider == "mock"
    assert settings.model.name == "mock-reviewer-v1"
    assert settings.budget.currency == "CNY"
    assert settings.budget.limit == 10.0
    assert settings.limits.max_diff_bytes == 5242880
    assert settings.storage.db == "runs/cra.sqlite"


def test_missing_config_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.yaml")
