"""Environment-variable-based configuration with validation.

All settings follow 12-factor app conventions. Every value has a sensible
default that works out of the box — users only override what they need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from graphrag_mcp.utils.errors import ConfigError


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {key}={raw!r} is not a valid integer.") from exc


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {key}={raw!r} is not a valid float.") from exc


@dataclass(frozen=True, slots=True)
class Config:
    """Immutable application configuration.

    Constructed once at startup from environment variables. Validated
    eagerly so misconfigurations surface immediately, not at runtime.
    """

    # ── Storage backend ──────────────────────────────────────────────────
    backend_type: str = field(default_factory=lambda: _env("GRAPHRAG_BACKEND_TYPE", "sqlite"))

    # ── Database ─────────────────────────────────────────────────────────
    db_path: Path = field(
        default_factory=lambda: Path(_env("GRAPHRAG_DB_PATH", ".graphrag/graph.db"))
    )

    # ── Embedding model ──────────────────────────────────────────────────
    embedding_model: str = field(
        default_factory=lambda: _env("GRAPHRAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    )
    use_onnx: bool = field(default_factory=lambda: _env_bool("GRAPHRAG_USE_ONNX", True))
    embedding_device: str = field(default_factory=lambda: _env("GRAPHRAG_EMBEDDING_DEVICE", "cpu"))
    cache_size: int = field(default_factory=lambda: _env_int("GRAPHRAG_CACHE_SIZE", 10000))

    # ── Search defaults ──────────────────────────────────────────────────
    search_limit: int = field(default_factory=lambda: _env_int("GRAPHRAG_SEARCH_LIMIT", 10))
    max_hops: int = field(default_factory=lambda: _env_int("GRAPHRAG_MAX_HOPS", 4))

    # ── Search tuning ────────────────────────────────────────────────────
    rrf_alpha: float = field(default_factory=lambda: _env_float("GRAPHRAG_RRF_ALPHA", 0.5))
    obs_boost: float = field(default_factory=lambda: _env_float("GRAPHRAG_OBS_BOOST", 0.5))

    # ── Logging ──────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: _env("GRAPHRAG_LOG_LEVEL", "WARNING"))

    # ── Transport ────────────────────────────────────────────────────────
    transport: str = field(default_factory=lambda: _env("GRAPHRAG_TRANSPORT", "stdio"))

    def __post_init__(self) -> None:
        """Validate all configuration values."""
        valid_backends = {"sqlite"}
        if self.backend_type not in valid_backends:
            raise ConfigError(
                f"GRAPHRAG_BACKEND_TYPE must be one of {valid_backends}, got {self.backend_type!r}"
            )
        if self.cache_size < 0:
            raise ConfigError(f"GRAPHRAG_CACHE_SIZE must be >= 0, got {self.cache_size}")
        if self.search_limit < 1:
            raise ConfigError(f"GRAPHRAG_SEARCH_LIMIT must be >= 1, got {self.search_limit}")
        if self.max_hops < 1:
            raise ConfigError(f"GRAPHRAG_MAX_HOPS must be >= 1, got {self.max_hops}")
        if not (0.0 <= self.rrf_alpha <= 1.0):
            raise ConfigError(
                f"GRAPHRAG_RRF_ALPHA must be between 0.0 and 1.0, got {self.rrf_alpha}"
            )
        if self.obs_boost < 0.0:
            raise ConfigError(f"GRAPHRAG_OBS_BOOST must be >= 0.0, got {self.obs_boost}")
        valid_transports = {"stdio", "sse", "streamable-http"}
        if self.transport not in valid_transports:
            raise ConfigError(
                f"GRAPHRAG_TRANSPORT must be one of {valid_transports}, got {self.transport!r}"
            )
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            raise ConfigError(
                f"GRAPHRAG_LOG_LEVEL must be one of {valid_levels}, got {self.log_level!r}"
            )
        valid_devices = {"cpu", "cuda"}
        if self.embedding_device not in valid_devices:
            raise ConfigError(
                f"GRAPHRAG_EMBEDDING_DEVICE must be one of {valid_devices}, "
                f"got {self.embedding_device!r}"
            )

    def ensure_db_dir(self) -> Path:
        """Create the database parent directory if it doesn't exist.

        Returns the resolved absolute path to the database file.
        """
        resolved = self.db_path.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved


def load_config() -> Config:
    return Config()
