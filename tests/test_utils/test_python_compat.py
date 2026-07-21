"""Guard the minimum Python version this project claims to support.

``requires-python = ">=3.10"`` is a promise, and the only place it is genuinely
observed is the CI matrix. This module adds a cheap static check so a too-new
standard-library API fails fast on a developer's newer interpreter rather than
only in CI.

It exists because it happened twice. First ``asyncio.timeout`` (3.11+) in a
concurrency test, which passed on 3.13 and failed the release on 3.10. Then the
first version of *this file* imported ``tomllib`` — also 3.11+ — and broke
collection on 3.10, which is the very failure it was written to prevent. Both
mistakes shared one cause: the checking code only ever ran on a new interpreter.

So this module imports nothing newer than 3.10, and checks imports as well as
attribute access.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

# Standard-library *modules* added after the declared floor. Importing one is an
# immediate collection error on an older interpreter, which makes these more
# damaging than the attribute accesses below.
TOO_NEW_MODULES = {
    "tomllib": "3.11 — parse the value with re, or depend on tomli",
    "asyncio.taskgroups": "3.11 — use asyncio.gather",
}

# Standard-library *attributes* added after the floor, written as they appear in
# source. Each value names the replacement so a failure is actionable.
TOO_NEW_ATTRIBUTES = {
    "asyncio.timeout": "3.11 — use asyncio.wait_for",
    "asyncio.timeout_at": "3.11 — use asyncio.wait_for",
    "asyncio.TaskGroup": "3.11 — use asyncio.gather",
    "asyncio.Runner": "3.11 — use asyncio.run",
    "contextlib.chdir": "3.11 — save and restore os.getcwd() yourself",
    "datetime.UTC": "3.11 — use datetime.timezone.utc",
    "enum.StrEnum": "3.11 — subclass (str, Enum)",
    "enum.ReprEnum": "3.11 — subclass Enum",
    "itertools.batched": "3.12 — slice manually",
    "typing.override": "3.12 — drop the decorator",
    "typing.TypeAliasType": "3.12 — use a plain alias",
}

SEARCH_ROOTS = ("src", "tests", "benchmarks")


def _declared_minimum() -> tuple[int, int]:
    """Read ``requires-python`` from pyproject without a TOML parser.

    Deliberately a regex: ``tomllib`` is 3.11+, and this module must import
    cleanly on the oldest version the project supports — otherwise it fails
    collection on exactly the interpreter it exists to protect.
    """
    text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*"[>=~^]*\s*(\d+)\.(\d+)', text, re.M)
    assert match, "could not find requires-python in pyproject.toml"
    return int(match.group(1)), int(match.group(2))


def _python_files() -> list[pathlib.Path]:
    return sorted(p for root in SEARCH_ROOTS for p in pathlib.Path(root).rglob("*.py"))


def _imported_names(node: ast.AST) -> list[str]:
    """Return the module names an import statement brings in."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    return []


def test_declared_minimum_is_still_3_10() -> None:
    """If the floor moves, the tables above must be revisited."""
    assert _declared_minimum() == (3, 10)


def test_this_module_imports_nothing_newer_than_the_floor() -> None:
    """The checker itself must run on the oldest supported interpreter.

    Regression: this file imported ``tomllib`` (3.11+), so on 3.10 it raised
    ModuleNotFoundError during collection and took the whole suite down with
    it — the exact failure it was written to catch.
    """
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    imported = {name for node in ast.walk(tree) for name in _imported_names(node)}

    too_new = imported & set(TOO_NEW_MODULES)
    assert not too_new, f"this module imports something newer than the floor: {too_new}"


@pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="on 3.10 the interpreter rejects these itself, so the scan is redundant",
)
def test_no_file_uses_a_stdlib_api_newer_than_the_floor() -> None:
    """No file may use a stdlib module or attribute newer than the floor.

    Parses rather than imports, so it covers code paths no test happens to
    execute.
    """
    offenders: list[str] = []
    this_file = pathlib.Path(__file__).resolve()

    for path in _python_files():
        # This module names the forbidden APIs in its own tables.
        if path.resolve() == this_file:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for name in _imported_names(node):
                if name in TOO_NEW_MODULES:
                    offenders.append(
                        f"{path}:{node.lineno}: imports {name} — {TOO_NEW_MODULES[name]}"
                    )

            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                dotted = f"{node.value.id}.{node.attr}"
                if dotted in TOO_NEW_ATTRIBUTES:
                    offenders.append(
                        f"{path}:{node.lineno}: {dotted} — {TOO_NEW_ATTRIBUTES[dotted]}"
                    )

    assert not offenders, "Python APIs newer than the supported floor:\n  " + "\n  ".join(offenders)
