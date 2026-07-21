"""Trust-boundary tests for the multi-graph tools.

``switch_graph``, ``create_graph``, and ``delete_graph`` each turn a
caller-supplied string into a filesystem path.  The caller is a language model,
so these tests treat that string as hostile input and assert on the *filesystem
side effects*, not just on the returned error dict — a tool that reports an
error but still deleted the file has not been fixed.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path

import pytest

import graph_mem.server as server_mod
from graph_mem.tools._core import _op_guard
from graph_mem.tools.graph_mgmt import (
    _MAX_GRAPH_NAME_LENGTH,
    _resolve_graph_path,
    create_graph,
    delete_graph,
    list_graphs,
    switch_graph,
)
from graph_mem.utils import ValidationError

# Names that must never be accepted.  Each one, if resolved naively by
# ``graphmem_dir / f"{name}.db"``, addresses a file outside .graphmem/.
ESCAPING_NAMES = [
    "../outside",
    "../../outside",
    "../../../etc/passwd",
    "sub/nested",
    "sub\\nested",
    "/absolute/path",
    "/etc/hosts",
    "~/secrets",
    ".",
    "..",
]

# Names that are well-formed but must be rejected for other reasons.
MALFORMED_NAMES = [
    "",
    "has space",
    "has.dot",
    "trailing;semicolon",
    "quote'name",
    "star*",
    "null\x00byte",
    "unicode-é",
]


# ---------------------------------------------------------------------------
# _resolve_graph_path — the single mapping every tool goes through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_resolve_graph_path_escaping_name_raises(setup_server, name):
    """Any name that could address a file outside .graphmem/ is rejected."""
    with pytest.raises(ValidationError):
        _resolve_graph_path(name)


@pytest.mark.parametrize("name", MALFORMED_NAMES)
def test_resolve_graph_path_malformed_name_raises(setup_server, name):
    """Names outside the grammar are rejected."""
    with pytest.raises(ValidationError):
        _resolve_graph_path(name)


def test_resolve_graph_path_reserved_stem_raises(setup_server):
    """'graph' is the default graph's file stem and cannot be addressed."""
    with pytest.raises(ValidationError, match="reserved"):
        _resolve_graph_path("graph")


def test_resolve_graph_path_default_maps_to_graph_db(setup_server):
    """'default' resolves to graph.db inside .graphmem/."""
    graphmem_dir = setup_server
    assert _resolve_graph_path("default") == (graphmem_dir / "graph.db").resolve()


def test_resolve_graph_path_named_graph_maps_to_own_file(setup_server):
    """A named graph resolves to <name>.db inside .graphmem/."""
    graphmem_dir = setup_server
    assert _resolve_graph_path("research") == (graphmem_dir / "research.db").resolve()


def test_resolve_graph_path_at_max_length_is_accepted(setup_server):
    """A name of exactly the maximum length is valid — the bound is inclusive."""
    name = "a" * _MAX_GRAPH_NAME_LENGTH
    graphmem_dir = setup_server
    assert _resolve_graph_path(name) == (graphmem_dir / f"{name}.db").resolve()


def test_resolve_graph_path_over_max_length_raises(setup_server):
    """One character past the maximum length is rejected."""
    with pytest.raises(ValidationError, match="at most"):
        _resolve_graph_path("a" * (_MAX_GRAPH_NAME_LENGTH + 1))


# ---------------------------------------------------------------------------
# delete_graph — the destructive path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES)
async def test_delete_graph_escaping_name_leaves_target_file_intact(
    setup_server, tmp_path, name
):
    """A traversal name returns an error and unlinks nothing."""
    graphmem_dir = setup_server
    # A database sitting where a naive join would land, one level above
    # .graphmem/ — exactly what "../outside" would address.
    victim = graphmem_dir.parent / "outside.db"
    victim.write_bytes(b"do not delete me")

    result = await delete_graph(name)

    assert result["error"] is True
    assert result["error_type"] == "ValidationError"
    assert victim.exists()
    assert victim.read_bytes() == b"do not delete me"


async def test_delete_graph_reserved_stem_cannot_delete_active_default(setup_server):
    """delete_graph('graph') must not unlink the active default database.

    'default' and 'graph' both used to map to graph.db, so a guard written
    against the active graph's *name* let the alias through.
    """
    graphmem_dir = setup_server
    default_db = graphmem_dir / "graph.db"
    assert default_db.exists()

    result = await delete_graph("graph")

    assert result["error"] is True
    assert default_db.exists()


async def test_delete_graph_active_graph_is_refused(setup_server):
    """The active graph cannot be deleted under its own name either."""
    graphmem_dir = setup_server
    default_db = graphmem_dir / "graph.db"

    result = await delete_graph("default")

    assert result["error"] is True
    assert "active graph" in result["message"]
    assert default_db.exists()


async def test_delete_graph_removes_only_the_named_graph(setup_server):
    """A valid, non-active graph is deleted and its siblings are untouched."""
    graphmem_dir = setup_server
    doomed = graphmem_dir / "doomed.db"
    keeper = graphmem_dir / "keeper.db"
    doomed.write_bytes(b"")
    keeper.write_bytes(b"")

    result = await delete_graph("doomed")

    assert "error" not in result
    assert result["status"] == "deleted"
    assert not doomed.exists()
    assert keeper.exists()
    assert (graphmem_dir / "graph.db").exists()


async def test_delete_graph_removes_wal_and_shm_sidecars(setup_server):
    """WAL-mode sidecar files are removed alongside the database."""
    graphmem_dir = setup_server
    base = graphmem_dir / "sidecars.db"
    base.write_bytes(b"")
    wal = Path(str(base) + "-wal")
    shm = Path(str(base) + "-shm")
    wal.write_bytes(b"")
    shm.write_bytes(b"")

    result = await delete_graph("sidecars")

    assert sorted(result["deleted_files"]) == ["sidecars.db", "sidecars.db-shm", "sidecars.db-wal"]
    assert not base.exists()
    assert not wal.exists()
    assert not shm.exists()


# ---------------------------------------------------------------------------
# switch_graph — the "open an arbitrary database" path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES)
async def test_switch_graph_escaping_name_leaves_target_file_intact(setup_server, name):
    """A traversal name is refused without opening or writing the target."""
    graphmem_dir = setup_server
    victim = graphmem_dir.parent / "outside.db"
    original = b"not a graph-mem database"
    victim.write_bytes(original)

    result = await switch_graph(name)

    assert result["error"] is True
    assert result["error_type"] == "ValidationError"
    # Unchanged bytes prove no schema/migration was written into it.
    assert victim.read_bytes() == original
    assert server_mod._state._active_graph == "default"


async def test_switch_graph_reserved_stem_is_refused(setup_server):
    """'graph' is refused; the active graph is unchanged."""
    result = await switch_graph("graph")

    assert result["error"] is True
    assert server_mod._state._active_graph == "default"


async def test_switch_graph_missing_graph_is_refused(setup_server):
    """A well-formed name with no file behind it is a NotFound, not a create."""
    graphmem_dir = setup_server

    result = await switch_graph("nonexistent")

    assert result["error"] is True
    assert result["error_type"] == "NotFound"
    assert not (graphmem_dir / "nonexistent.db").exists()
    assert server_mod._state._active_graph == "default"


async def test_switch_graph_valid_graph_becomes_active(setup_server):
    """The happy path still works after validation was tightened."""
    created = await create_graph("research")
    assert "error" not in created

    result = await switch_graph("research")

    assert "error" not in result
    assert result["status"] == "switched"
    assert server_mod._state._active_graph == "research"


async def test_switch_graph_failure_leaves_previous_graph_usable(setup_server, monkeypatch):
    """If the new backend cannot be opened, the old one stays active and open.

    Regression: the previous implementation closed the current storage before
    opening the replacement, so a failure left every engine pointing at a
    closed handle and the server never served another request.
    """
    from graph_mem.tools import graph_mgmt

    created = await create_graph("brokengraph")
    assert "error" not in created

    healthy_storage = server_mod._state.storage

    class _FailingBackend:
        def __init__(self, *args, **kwargs) -> None:
            self.closed = False

        async def initialize(self) -> None:
            raise sqlite3.OperationalError("disk I/O error")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(graph_mgmt, "create_backend", lambda *a, **kw: _FailingBackend())

    result = await switch_graph("brokengraph")

    assert result["error"] is True
    # The engines must still reference the original, still-open backend.
    assert server_mod._state.storage is healthy_storage
    assert server_mod._state._active_graph == "default"
    # Prove it is genuinely usable, not merely non-None.
    assert await healthy_storage.count_entities() == 0


async def test_switch_graph_waits_for_in_flight_operations(setup_server):
    """A switch must not close storage while another tool call is using it.

    Regression: ``_switch_lock`` only serialised switch-against-switch, so a
    switch could close a backend that a concurrent write was mid-transaction
    on.
    """
    created = await create_graph("concurrent")
    assert "error" not in created

    op_started = asyncio.Event()
    release_op = asyncio.Event()
    storage_seen_by_op = []

    async def _in_flight_operation() -> None:
        async with _op_guard():
            storage_seen_by_op.append(server_mod._state.storage)
            op_started.set()
            await release_op.wait()
            # The backend this call captured must still be open when it
            # resumes, no matter what the switch is trying to do.
            assert await storage_seen_by_op[0].count_entities() == 0

    op_task = asyncio.create_task(_in_flight_operation())
    await op_started.wait()

    switch_task = asyncio.create_task(switch_graph("concurrent"))
    # Give the switch every chance to (incorrectly) proceed.
    await asyncio.sleep(0.05)
    assert not switch_task.done(), "switch_graph closed storage with an operation in flight"

    release_op.set()
    await op_task
    result = await switch_task

    assert "error" not in result
    assert server_mod._state._active_graph == "concurrent"


# ---------------------------------------------------------------------------
# create_graph
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [*ESCAPING_NAMES, *MALFORMED_NAMES])
async def test_create_graph_rejects_invalid_name(setup_server, name):
    """create_graph shares the same grammar as its destructive siblings."""
    result = await create_graph(name)
    assert result["error"] is True
    assert result["error_type"] == "ValidationError"


async def test_create_graph_reserved_stem_is_refused(setup_server):
    """The docstring promised 'graph' was reserved; now it is enforced."""
    result = await create_graph("graph")
    assert result["error"] is True
    assert result["error_type"] == "ValidationError"


async def test_create_graph_existing_graph_is_not_overwritten(setup_server):
    """Creating over an existing graph reports AlreadyExists and keeps bytes."""
    graphmem_dir = setup_server
    existing = graphmem_dir / "taken.db"
    existing.write_bytes(b"payload")

    result = await create_graph("taken")

    assert result["error"] is True
    assert result["error_type"] == "AlreadyExists"
    assert existing.read_bytes() == b"payload"


# ---------------------------------------------------------------------------
# list_graphs
# ---------------------------------------------------------------------------


async def test_list_graphs_reports_unreadable_file_as_error(setup_server):
    """A non-graph .db file is flagged, not reported as having -1 entities.

    Regression: counts failed silently to (-1, -1, -1), which an agent reads
    as real data.
    """
    graphmem_dir = setup_server
    junk = graphmem_dir / "junk.db"
    junk.write_bytes(b"this is not a sqlite database")

    result = await list_graphs()

    entry = next(g for g in result["graphs"] if g["name"] == "junk")
    assert entry["unreadable"] is True
    assert "error" in entry
    assert "entities" not in entry


async def test_list_graphs_reports_counts_for_readable_graph(setup_server):
    """A real graph reports actual counts and is marked active."""
    result = await list_graphs()

    entry = next(g for g in result["graphs"] if g["name"] == "default")
    assert entry["entities"] == 0
    assert entry["relationships"] == 0
    assert entry["observations"] == 0
    assert entry["active"] is True
    assert "unreadable" not in entry


@pytest.fixture(autouse=True)
def _restore_active_graph():
    """Keep a failed assertion from leaking active-graph state into the next test."""
    yield
    with contextlib.suppress(Exception):
        server_mod._state._active_graph = "default"
