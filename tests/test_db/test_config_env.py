"""Tests for Config environment variable handling and _resolve_db_path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from graphrag_mcp.utils.config import Config, ConfigError

# ── GRAPHRAG_BACKEND_TYPE ───────────────────────────────────────────────────


def test_config_backend_type_default() -> None:
    """Default backend type is 'sqlite'."""
    config = Config()
    assert config.backend_type == "sqlite"


def test_config_backend_type_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GRAPHRAG_BACKEND_TYPE env var is picked up."""
    monkeypatch.setenv("GRAPHRAG_BACKEND_TYPE", "sqlite")
    config = Config()
    assert config.backend_type == "sqlite"


def test_config_backend_type_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid GRAPHRAG_BACKEND_TYPE raises ConfigError."""
    monkeypatch.setenv("GRAPHRAG_BACKEND_TYPE", "neo4j")
    with pytest.raises(ConfigError, match="GRAPHRAG_BACKEND_TYPE"):
        Config()


# ── GRAPHRAG_USE_ONNX ──────────────────────────────────────────────────────


def test_config_use_onnx_default_true() -> None:
    """Default use_onnx is True."""
    config = Config()
    assert config.use_onnx is True


def test_config_use_onnx_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_USE_ONNX", "false")
    config = Config()
    assert config.use_onnx is False


def test_config_use_onnx_env_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_USE_ONNX", "yes")
    config = Config()
    assert config.use_onnx is True


# ── GRAPHRAG_CACHE_SIZE ────────────────────────────────────────────────────


def test_config_cache_size_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_CACHE_SIZE", "500")
    config = Config()
    assert config.cache_size == 500


def test_config_cache_size_env_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_CACHE_SIZE", "not_a_number")
    with pytest.raises(ConfigError, match="not a valid integer"):
        Config()


# ── GRAPHRAG_EMBEDDING_MODEL ──────────────────────────────────────────────


def test_config_embedding_model_default() -> None:
    config = Config()
    assert config.embedding_model == "all-MiniLM-L6-v2"


def test_config_embedding_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_EMBEDDING_MODEL", "custom-model-v3")
    config = Config()
    assert config.embedding_model == "custom-model-v3"


# ── GRAPHRAG_LOG_LEVEL ────────────────────────────────────────────────────


def test_config_log_level_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_LOG_LEVEL", "DEBUG")
    config = Config()
    assert config.log_level == "DEBUG"


# ── GRAPHRAG_SEARCH_LIMIT / MAX_HOPS ──────────────────────────────────────


def test_config_search_limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_SEARCH_LIMIT", "50")
    config = Config()
    assert config.search_limit == 50


def test_config_max_hops_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAG_MAX_HOPS", "8")
    config = Config()
    assert config.max_hops == 8


# ── Config.ensure_db_dir ──────────────────────────────────────────────────


def test_ensure_db_dir_creates_parents(tmp_path: Path) -> None:
    """ensure_db_dir creates all intermediate directories."""
    db_path = tmp_path / "a" / "b" / "c" / "graph.db"
    config = Config(db_path=db_path)
    resolved = config.ensure_db_dir()
    assert resolved.parent.exists()
    assert resolved.parent == (tmp_path / "a" / "b" / "c").resolve()


def test_ensure_db_dir_returns_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_db_dir returns an absolute path even for relative input."""
    monkeypatch.chdir(tmp_path)
    config = Config(db_path=Path(".graphrag/graph.db"))
    resolved = config.ensure_db_dir()
    assert resolved.is_absolute()
    assert str(resolved).startswith(str(tmp_path))


# ── _resolve_db_path helper ──────────────────────────────────────────────


def test_resolve_db_path_db_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--db flag sets GRAPHRAG_DB_PATH."""
    from graphrag_mcp.cli.main import _resolve_db_path

    monkeypatch.delenv("GRAPHRAG_DB_PATH", raising=False)
    db_file = tmp_path / "explicit.db"
    _resolve_db_path(db_path=str(db_file))
    assert os.environ["GRAPHRAG_DB_PATH"] == str(db_file.resolve())


def test_resolve_db_path_project_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--project-dir sets GRAPHRAG_DB_PATH to <dir>/.graphrag/graph.db."""
    from graphrag_mcp.cli.main import _resolve_db_path

    monkeypatch.delenv("GRAPHRAG_DB_PATH", raising=False)
    _resolve_db_path(project_dir=str(tmp_path))
    expected = str((tmp_path / ".graphrag" / "graph.db").resolve())
    assert os.environ["GRAPHRAG_DB_PATH"] == expected


def test_resolve_db_path_db_overrides_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--db takes priority over --project-dir."""
    from graphrag_mcp.cli.main import _resolve_db_path

    monkeypatch.delenv("GRAPHRAG_DB_PATH", raising=False)
    explicit = tmp_path / "override.db"
    _resolve_db_path(db_path=str(explicit), project_dir=str(tmp_path))
    assert os.environ["GRAPHRAG_DB_PATH"] == str(explicit.resolve())


def test_resolve_db_path_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither --db nor --project-dir leaves GRAPHRAG_DB_PATH untouched."""
    from graphrag_mcp.cli.main import _resolve_db_path

    monkeypatch.delenv("GRAPHRAG_DB_PATH", raising=False)
    _resolve_db_path()
    assert "GRAPHRAG_DB_PATH" not in os.environ


def test_resolve_db_path_preserves_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When neither flag is given, existing env var is preserved."""
    from graphrag_mcp.cli.main import _resolve_db_path

    monkeypatch.setenv("GRAPHRAG_DB_PATH", "/existing/db.sqlite")
    _resolve_db_path()
    assert os.environ["GRAPHRAG_DB_PATH"] == "/existing/db.sqlite"


# ── Frozen dataclass ─────────────────────────────────────────────────────


def test_config_is_frozen() -> None:
    """Config is immutable after construction."""
    config = Config()
    with pytest.raises(AttributeError):
        config.cache_size = 999  # type: ignore[misc]
