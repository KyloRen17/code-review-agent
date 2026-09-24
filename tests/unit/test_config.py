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


def test_load_settings_with_overrides(tmp_path, agent_config_path):
    from code_review_agent.cli import _load_settings_with_overrides

    settings = _load_settings_with_overrides(
        agent_config_path, tmp_path / "db.sqlite", tmp_path / "runs", "openai", "qwen3.8-flash"
    )
    assert settings.model.provider == "openai"
    assert settings.model.name == "qwen3.8-flash"
    assert settings.storage.db == str(tmp_path / "db.sqlite")
    assert settings.storage.report_dir == str(tmp_path / "runs")


def test_load_settings_overrides_keep_defaults_when_none(agent_config_path):
    from code_review_agent.cli import _load_settings_with_overrides

    settings = _load_settings_with_overrides(agent_config_path, None, None, None, None)
    assert settings.model.provider == "mock"
    assert settings.model.name == "mock-reviewer-v1"
