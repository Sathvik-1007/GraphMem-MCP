"""Guard the minimum Python version this project claims to support.

`requires-python = ">=3.10"` is a promise, and the only place it is actually
observed is the CI matrix — which runs the suite on 3.10 through 3.13. This
module adds a cheap static check so a 3.11+ API used in *test* code fails fast
locally on a newer interpreter, rather than only in CI.

It exists because it happened: `asyncio.timeout` (3.11+) was used in a
concurrency test, passed on 3.13 locally, and failed the release on 3.10.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest
import tomllib

# Names added to the standard library after our minimum version. Each entry is
# the attribute access as written in source, mapped to the version that
# introduced it, so a failure message can say what to use instead.
_TOO_NEW = {
    "asyncio.timeout": "3.11 — use asyncio.wait_for",
    "asyncio.timeout_at": "3.11 — use asyncio.wait_for",
    "asyncio.TaskGroup": "3.11 — use asyncio.gather",
    "itertools.batched": "3.12 — slice manually",
    "typing.override": "3.12 — drop the decorator",
    "enum.StrEnum": "3.11 — subclass (str, Enum)",
    "datetime.UTC": "3.11 — use datetime.timezone.utc",
}

_ROOTS = ("src", "tests", "benchmarks")


def _minimum_supported_version() -> tuple[int, int]:
    """Read the floor from pyproject rather than hard-coding it here."""
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    spec = data["project"]["requires-python"]
    major, minor = spec.removeprefix(">=").strip().split(".")[:2]
    return int(major), int(minor)


def _python_files() -> list[pathlib.Path]:
    return [p for root in _ROOTS for p in pathlib.Path(root).rglob("*.py")]


def test_declared_minimum_is_still_3_10() -> None:
    """If the floor moves, this module's exclusion list must be revisited."""
    assert _minimum_supported_version() == (3, 10)


@pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="on 3.10 the interpreter itself rejects these, so the check is redundant",
)
def test_no_source_file_uses_a_too_new_stdlib_api() -> None:
    """No file may call a stdlib API newer than the declared minimum.

    Checked by parsing rather than importing, so it covers code paths that
    never run under the current test selection.
    """
    offenders: list[str] = []

    for path in _python_files():
        # This module names the forbidden APIs in a dict; skip itself.
        if path.name == "test_python_compat.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in _TOO_NEW:
                offenders.append(f"{path}:{node.lineno}: {dotted} needs {_TOO_NEW[dotted]}")

    assert not offenders, "Python APIs newer than the supported floor:\n  " + "\n  ".join(offenders)
