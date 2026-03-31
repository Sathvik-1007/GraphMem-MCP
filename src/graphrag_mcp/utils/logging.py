"""Structured logging setup for graphrag-mcp.

Uses the standard library logging module with a consistent format.
Log level is controlled by GRAPHRAG_LOG_LEVEL (default: WARNING).
"""

from __future__ import annotations

import logging
import sys


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "WARNING") -> None:
    """Configure the root graphrag_mcp logger.

    Safe to call multiple times.  Reconfigures when the requested level
    differs from the current one; otherwise is a no-op.
    """
    root = logging.getLogger("graphrag_mcp")
    target_level = getattr(logging, level.upper(), logging.WARNING)

    if root.level == target_level and root.handlers:
        return  # Already configured at this level

    root.setLevel(target_level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)

    # Suppress noisy third-party loggers
    for name in ("sentence_transformers", "transformers", "torch", "onnxruntime"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"graphrag_mcp.{name}")
