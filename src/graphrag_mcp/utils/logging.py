"""Structured logging setup for graphrag-mcp.

Uses the standard library logging module with a consistent format.
Log level is controlled by GRAPHRAG_LOG_LEVEL (default: WARNING).
"""

from __future__ import annotations

import logging
import sys


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(level: str = "WARNING") -> None:
    """Configure the root graphrag_mcp logger.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _initialized  # noqa: PLW0603
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger("graphrag_mcp")
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))

    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)

    # Suppress noisy third-party loggers
    for name in ("sentence_transformers", "transformers", "torch", "onnxruntime"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the graphrag_mcp namespace."""
    return logging.getLogger(f"graphrag_mcp.{name}")
