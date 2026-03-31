"""Tests for graphrag_mcp.utils.logging — structured logging setup."""

from __future__ import annotations

import logging

from graphrag_mcp.utils.logging import get_logger, setup_logging


def test_setup_logging_creates_handler():
    """setup_logging configures the root graphrag_mcp logger."""
    root = logging.getLogger("graphrag_mcp")
    root.handlers.clear()

    setup_logging("DEBUG")

    assert root.level == logging.DEBUG
    assert len(root.handlers) >= 1


def test_setup_logging_idempotent():
    """Calling setup_logging twice at the same level is a no-op."""
    root = logging.getLogger("graphrag_mcp")
    root.handlers.clear()

    setup_logging("INFO")
    handler_count = len(root.handlers)

    setup_logging("INFO")
    assert len(root.handlers) == handler_count


def test_setup_logging_reconfigures_level():
    """setup_logging changes the level when called with a new value."""
    setup_logging("WARNING")
    root = logging.getLogger("graphrag_mcp")
    assert root.level == logging.WARNING

    setup_logging("ERROR")
    assert root.level == logging.ERROR


def test_get_logger_returns_child():
    """get_logger returns a child of graphrag_mcp."""
    log = get_logger("test_child")
    assert log.name == "graphrag_mcp.test_child"
    assert isinstance(log, logging.Logger)


def test_noisy_loggers_suppressed():
    """Third-party loggers are set to WARNING."""
    setup_logging("DEBUG")
    for name in ("sentence_transformers", "transformers", "torch", "onnxruntime"):
        assert logging.getLogger(name).level >= logging.WARNING
