from __future__ import annotations

import os

from code_review_agent.config import load_env_file

_VARS = ["CRA_TEST_VAR", "CRA_TEST_INT", "CRA_TEST_CWD", "CRA_TEST_EXISTING"]


def _cleanup() -> None:
    for name in _VARS:
        os.environ.pop(name, None)


def test_missing_file_is_noop(tmp_path):
    _cleanup()
    try:
        assert load_env_file(tmp_path / "nope.env") == 0
        assert "CRA_TEST_VAR" not in os.environ
    finally:
        _cleanup()


def test_loads_key_values(tmp_path):
    _cleanup()
    env = tmp_path / ".env"
    env.write_text("CRA_TEST_VAR=hello\n", encoding="utf-8")
    try:
        assert load_env_file(env) == 1
        assert os.environ["CRA_TEST_VAR"] == "hello"
    finally:
        _cleanup()


def test_real_environment_wins_not_overridden(tmp_path):
    _cleanup()
    env = tmp_path / ".env"
    env.write_text("CRA_TEST_EXISTING=from_file\n", encoding="utf-8")
    os.environ["CRA_TEST_EXISTING"] = "from_env"
    try:
        assert load_env_file(env) == 0  # 已存在的真实变量不计入加载
        assert os.environ["CRA_TEST_EXISTING"] == "from_env"
    finally:
        _cleanup()


def test_export_prefix_quotes_comments_and_broken_lines(tmp_path):
    _cleanup()
    env = tmp_path / ".env"
    env.write_text(
        "# 注释行\n"
        "\n"
        "export CRA_TEST_VAR='quoted value'\n"
        "CRA_TEST_INT=\"42\"\n"
        "BROKEN_LINE_WITHOUT_EQUALS\n"
        "EMPTY_VALUE=\n",
        encoding="utf-8",
    )
    try:
        assert load_env_file(env) == 2
        assert os.environ["CRA_TEST_VAR"] == "quoted value"
        assert os.environ["CRA_TEST_INT"] == "42"
    finally:
        _cleanup()


def test_default_path_resolves_cwd_dotenv(tmp_path, monkeypatch):
    _cleanup()
    monkeypatch.chdir(tmp_path)  # 隔离：不触碰仓库根目录可能存在的真实 .env
    (tmp_path / ".env").write_text("CRA_TEST_CWD=1\n", encoding="utf-8")
    try:
        assert load_env_file(None) == 1
        assert os.environ["CRA_TEST_CWD"] == "1"
    finally:
        _cleanup()


def test_return_value_is_count_only_no_secret_leak(tmp_path):
    _cleanup()
    env = tmp_path / ".env"
    env.write_text("CRA_TEST_VAR=super-secret-value\n", encoding="utf-8")
    try:
        result = load_env_file(env)
        assert isinstance(result, int) and result == 1  # 结构上只返回计数，不含值
    finally:
        _cleanup()
