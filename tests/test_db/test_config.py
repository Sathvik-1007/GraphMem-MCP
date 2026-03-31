"""Tests for Config construction and validation."""

from __future__ import annotations

import pytest

from graphrag_mcp.utils.config import Config, ConfigError, load_config


def test_default_config_is_valid():
    config = Config()
    assert config.cache_size >= 0
    assert config.search_limit >= 1
    assert config.max_hops >= 1


def test_config_negative_cache_size():
    with pytest.raises(ConfigError, match="GRAPHRAG_CACHE_SIZE"):
        Config(cache_size=-1)


def test_config_zero_search_limit():
    with pytest.raises(ConfigError, match="GRAPHRAG_SEARCH_LIMIT"):
        Config(search_limit=0)


def test_config_zero_max_hops():
    with pytest.raises(ConfigError, match="GRAPHRAG_MAX_HOPS"):
        Config(max_hops=0)


def test_config_invalid_transport():
    with pytest.raises(ConfigError, match="GRAPHRAG_TRANSPORT"):
        Config(transport="websocket")


def test_config_invalid_log_level():
    with pytest.raises(ConfigError, match="GRAPHRAG_LOG_LEVEL"):
        Config(log_level="TRACE")


def test_config_invalid_device():
    with pytest.raises(ConfigError, match="GRAPHRAG_EMBEDDING_DEVICE"):
        Config(embedding_device="tpu")


def test_config_env_override(monkeypatch, tmp_path):
    db_path = str(tmp_path / "custom.db")
    monkeypatch.setenv("GRAPHRAG_DB_PATH", db_path)
    config = Config()
    assert str(config.db_path) == db_path


def test_config_ensure_db_dir(tmp_path):
    db_path = tmp_path / "sub" / "dir" / "graph.db"
    config = Config(db_path=db_path)
    resolved = config.ensure_db_dir()
    assert resolved.parent.exists()


def test_load_config():
    config = load_config()
    assert isinstance(config, Config)
