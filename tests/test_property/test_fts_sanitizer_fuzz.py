"""Fuzz tests for the FTS5 query sanitiser and the searches that depend on it.

``_sanitize_fts5_query`` is the only thing standing between arbitrary
model-supplied text and an FTS5 ``MATCH`` expression, where a stray double
quote turns the rest of the query into operators.  Two levels are fuzzed:

* the sanitiser itself, as a pure function — cheap, so it runs many examples
  and asserts on the *shape* of what it emits;
* ``search_nodes`` and ``search_observations``, end to end against a real
  database, asserting only that nothing escapes.
"""

from __future__ import annotations

import asyncio
import itertools
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from hypothesis import example, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

import graph_mem.tools._core as core
from graph_mem.graph.engine import GraphEngine
from graph_mem.graph.merge import EntityMerger
from graph_mem.graph.traversal import GraphTraversal
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import HybridSearch
from graph_mem.storage import SQLiteBackend
from graph_mem.tools.search import search_nodes, search_observations
from graph_mem.utils.config import Config

if TYPE_CHECKING:
    from pathlib import Path

_db_counter = itertools.count()

# Inputs chosen to break an FTS5 expression rather than merely be unusual:
# every FTS5 operator, an unterminated phrase, a column filter, a NEAR call,
# control characters, NUL, and the whitespace-only / empty degenerate cases.
HOSTILE_QUERIES = [
    "",
    " ",
    "\t\n\r\v\f",
    '"',
    '"""',
    '""""',
    'alpha" OR "beta',
    'alpha" OR entities_fts MATCH "',
    "*",
    "^alpha",
    "name:alpha",
    "alpha:beta:gamma",
    "NEAR(alpha beta, 2)",
    "alpha NEAR beta",
    "alpha AND beta",
    "alpha OR beta",
    "alpha NOT beta",
    "(alpha OR beta) AND gamma",
    ")",
    "(((",
    "-alpha",
    "alpha -beta",
    "alpha*",
    "\x00",
    "alpha\x00beta",
    "\x01\x02\x03\x1b",
    "café naïve 北京 🙂",
    "Ⅻ ﬁ ǅ İ",
    "'" * 32,
    "a" * 20_000,
    "alpha " * 4_000,
    '" OR 1=1 --',
    "'; DROP TABLE entities; --",
]

# st.text() alone rarely produces an operator, so the operator alphabet and the
# curated list are mixed in explicitly.
FTS_ALPHABET = "\"*^:()-+ \tANDORNTEabc01'\\/{}[]~"

queries = st.one_of(
    st.text(),
    st.sampled_from(HOSTILE_QUERIES),
    st.text(alphabet=FTS_ALPHABET, max_size=40),
    st.lists(st.sampled_from(HOSTILE_QUERIES), max_size=4).map(" ".join),
    st.text(alphabet=st.characters(codec="utf-8"), max_size=60),
)

# What a safe sanitiser output looks like: nothing but quoted runs of word
# characters and apostrophes, joined by OR.  Anything else means an operator
# survived tokenisation.
_SAFE_EXPRESSION = re.compile(r"\"[\w']*\"(?: OR \"[\w']*\")*")


def _sanitize(query: str) -> str:
    """Call the sanitiser on a backend that was never opened.

    ``_sanitize_fts5_query`` touches no state, so no database is needed and
    thousands of examples cost nothing.
    """
    from pathlib import Path as _Path

    return SQLiteBackend(_Path("unused.db"))._sanitize_fts5_query(query)


# ── The sanitiser as a pure function ─────────────────────────────────────────


@given(query=queries)
@hyp_settings(max_examples=400)
def test_sanitizer_never_emits_a_bare_double_quote(query: str) -> None:
    """Every quote the sanitiser emits is one it opened or closed itself.

    This is the property the whole defence rests on.  A single unpaired quote
    in the output would end the phrase early and hand the remainder of the
    user's text to the FTS5 expression parser as operators.
    """
    out = _sanitize(query)

    assert isinstance(out, str)
    assert out.count('"') % 2 == 0, f"odd number of quotes in {out!r}"
    assert _SAFE_EXPRESSION.fullmatch(out), f"unsafe FTS5 expression emitted: {out!r}"

    # Stated the other way round: no quote of the caller's ever survives, and
    # no operator character does either.
    for token in re.findall(r'"([^"]*)"', out):
        assert '"' not in token
        assert not set(token) & set("*^:()-+ \t\n")


@given(query=queries)
@hyp_settings(max_examples=200)
def test_sanitizer_is_idempotent_and_total(query: str) -> None:
    """Sanitising an already-sanitised expression cannot re-open the hole."""
    once = _sanitize(query)
    twice = _sanitize(once)
    assert _SAFE_EXPRESSION.fullmatch(twice)
    assert twice.count('"') % 2 == 0


# ── End to end through the real FTS5 index ───────────────────────────────────


async def _with_server(db_path: Path, body: Any) -> None:
    """Populate the module-level tool state over a temp DB, run *body*, reset.

    Mirrors ``tests/test_server/conftest.py``'s fixture.  It is inlined rather
    than shared because a function-scoped async fixture cannot be re-entered
    per hypothesis example.
    """
    storage = SQLiteBackend(db_path)
    await storage.initialize()
    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
    graph = GraphEngine(storage)

    core._state.storage = storage
    core._state.graph = graph
    core._state.traversal = GraphTraversal(storage)
    core._state.merger = EntityMerger(storage)
    core._state.embeddings = embeddings
    core._state.search = HybridSearch(storage, embeddings)
    core._state.config = Config(db_path=db_path)
    core._state._graphmem_dir = db_path.parent
    core._state._active_graph = "default"

    try:
        # Real rows, so the FTS index is non-empty and MATCH actually runs.
        from graph_mem.models.entity import Entity
        from graph_mem.models.observation import Observation

        await graph.add_entities(
            [
                Entity(name="Alpha Project", entity_type="project", description="a NEAR b"),
                Entity(name='Beta "quoted" Thing', entity_type="concept", description="c*d"),
            ]
        )
        await graph.add_observations(
            "Alpha Project",
            [
                Observation.pending("alpha AND beta OR gamma"),
                Observation.pending('a "quoted" observation'),
            ],
        )
        await body()
    finally:
        await storage.close()
        for attr in (
            "storage",
            "graph",
            "traversal",
            "merger",
            "embeddings",
            "search",
            "config",
        ):
            setattr(core._state, attr, None)
        core._state._graphmem_dir = None
        core._state._active_graph = "default"


@given(query=queries)
@hyp_settings(max_examples=50)
@example(query="")
@example(query='alpha" OR "beta')
@example(query="\x00")
@example(query="a" * 20_000)
@example(query="alpha " * 4_000)
@example(query="NEAR(alpha beta, 2)")
def test_search_tools_never_raise_for_any_query(tmp_path: Path, query: str) -> None:
    """No input reaches SQLite as syntax.

    ``search_nodes`` and ``search_observations`` must either return results or
    return none.  In particular no ``sqlite3.OperationalError`` may escape —
    an FTS5 syntax error is the signature of an operator having survived the
    sanitiser.
    """
    db_path = tmp_path / f"fts{next(_db_counter):05d}.db"

    async def body() -> None:
        for tool, kwargs in (
            (search_nodes, {"limit": 3}),
            (search_observations, {"limit": 3}),
        ):
            try:
                result = await tool(query, **kwargs)  # type: ignore[operator]
            except sqlite3.OperationalError as exc:  # pragma: no cover - the bug case
                raise AssertionError(
                    f"{tool.__name__} leaked an FTS5 error for {query!r}: {exc}"
                ) from exc

            assert isinstance(result, dict)
            assert "error" not in result, f"{tool.__name__} errored on {query!r}: {result}"
            assert isinstance(result["results"], list)
            assert result["count"] == len(result["results"])

    asyncio.run(_with_server(db_path, body))


@given(query=queries)
@hyp_settings(max_examples=50)
def test_storage_fts_helpers_never_raise(tmp_path: Path, query: str) -> None:
    """The three storage-level FTS entry points swallow malformed expressions."""
    db_path = tmp_path / f"ftsraw{next(_db_counter):05d}.db"

    async def main() -> None:
        storage = SQLiteBackend(db_path)
        await storage.initialize()
        try:
            assert isinstance(await storage.fts_search_entities(query, 5), list)
            assert isinstance(await storage.fts_search_observations(query, 5), list)
            assert isinstance(await storage.fts_suggest_similar(query, 5), list)
        finally:
            await storage.close()

    asyncio.run(main())
