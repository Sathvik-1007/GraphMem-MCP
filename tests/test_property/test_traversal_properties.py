"""Property tests for :class:`GraphTraversal` against a brute-force reference.

The reference BFS below is written from the definition of breadth-first search
over a plain adjacency dict.  It shares no code with
``graph_mem.graph.traversal`` — no imports, no helpers, no data structures — so
agreement between the two is evidence about the implementation rather than a
tautology.

Every example builds a real SQLite database with the real schema and runs the
real queries.  Rows are inserted with ``execute_many`` rather than through
``upsert_entity`` / ``upsert_relationship`` purely for speed: the generated
edge set is already deduplicated the same way the unique index would
deduplicate it, so the stored graph is identical either way.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import TYPE_CHECKING, Any

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from graph_mem.graph.traversal import MAX_SHORTEST_PATHS, GraphTraversal
from graph_mem.storage import SQLiteBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable, Sequence
    from pathlib import Path

REL_TYPES = ("knows", "likes", "owns")
DIRECTIONS = ("outgoing", "incoming", "both")

Edge = tuple[str, str, str]

_db_counter = itertools.count()


# ── Brute-force reference implementation ─────────────────────────────────────


def _reference_adjacency(
    edges: Iterable[Edge],
    *,
    direction: str,
    rel_types: Sequence[str] | None = None,
) -> dict[str, list[tuple[str, str, str]]]:
    """Build ``node -> [(neighbour, relationship_type, step_direction), ...]``.

    Following an ``outgoing`` edge means stepping from its source to its
    target; ``incoming`` means the reverse; ``both`` allows either.
    """
    adjacency: dict[str, list[tuple[str, str, str]]] = {}
    for source, target, rel_type in edges:
        if rel_types is not None and rel_type not in rel_types:
            continue
        if direction in ("outgoing", "both"):
            adjacency.setdefault(source, []).append((target, rel_type, "outgoing"))
        if direction in ("incoming", "both"):
            adjacency.setdefault(target, []).append((source, rel_type, "incoming"))
    return adjacency


def _reference_depths(
    adjacency: dict[str, list[tuple[str, str, str]]],
    seeds: Sequence[str],
    max_hops: int,
) -> dict[str, int]:
    """Minimum hop count from the nearest seed, for every node reached.

    Textbook BFS: one global visited set, expanded strictly level by level, so
    the first time a node is seen it is at its minimal depth.
    """
    depth: dict[str, int] = {}
    frontier: list[str] = []
    for seed in seeds:
        if seed not in depth:
            depth[seed] = 0
            frontier.append(seed)

    for hop in range(1, max_hops + 1):
        if not frontier:
            break
        next_frontier: list[str] = []
        for node in frontier:
            for neighbour, _rel_type, _step_dir in adjacency.get(node, ()):
                if neighbour not in depth:
                    depth[neighbour] = hop
                    next_frontier.append(neighbour)
        frontier = next_frontier

    return depth


# ── Graph generation ─────────────────────────────────────────────────────────


def _node_id(index: int) -> str:
    return f"n{index:02d}"


@st.composite
def graph_specs(draw: st.DrawFn) -> tuple[int, list[Edge]]:
    """Random directed multigraphs: 1-25 nodes, varying shape and density.

    A purely random edge list over 25 nodes is almost always sparse and
    shallow, which leaves ``max_hops`` untested — every target sits one hop
    away.  Drawing a *shape* first fixes that: chains and trees produce
    diameters well past the six-hop ceiling the tests sweep, while the dense
    shape produces the opposite extreme.

    Self-loops, parallel edges of different types, cycles, and disconnected
    components all arise from the unconstrained extra edges layered on top.
    Edges are deduplicated on ``(source, target, type)`` to match the unique
    index the relationships table carries.
    """
    node_count = draw(st.integers(min_value=1, max_value=25))
    shape = draw(st.sampled_from(("random", "chain", "cycle", "tree", "dense")))

    endpoints = st.integers(min_value=0, max_value=node_count - 1)
    pairs: set[tuple[int, int, str]] = set()

    if shape in ("chain", "cycle"):
        for i in range(node_count - 1):
            pairs.add((i, i + 1, draw(st.sampled_from(REL_TYPES))))
        if shape == "cycle" and node_count > 1:
            pairs.add((node_count - 1, 0, draw(st.sampled_from(REL_TYPES))))
    elif shape == "tree":
        for i in range(1, node_count):
            parent = draw(st.integers(min_value=0, max_value=i - 1))
            pairs.add((parent, i, draw(st.sampled_from(REL_TYPES))))

    floor = min(node_count * 2, 40) if shape == "dense" else 0
    ceiling = 60 if shape == "dense" else 12
    pairs.update(
        draw(
            st.lists(
                st.tuples(endpoints, endpoints, st.sampled_from(REL_TYPES)),
                min_size=floor,
                max_size=max(floor, ceiling),
            )
        )
    )

    edges = sorted({(_node_id(a), _node_id(b), rel) for a, b, rel in pairs})
    return node_count, edges


async def _build_graph(db_path: Path, node_count: int, edges: list[Edge]) -> SQLiteBackend:
    """Create a backend holding exactly *node_count* nodes and *edges* edges."""
    storage = SQLiteBackend(db_path)
    await storage.initialize()
    now = 1_700_000_000.0
    await storage.db.execute_many(
        "INSERT INTO entities "
        "(id, name, entity_type, description, properties, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(_node_id(i), f"node-{i:02d}", "thing", "", "{}", now, now) for i in range(node_count)],
    )
    if edges:
        await storage.db.execute_many(
            "INSERT INTO relationships "
            "(id, source_id, target_id, relationship_type, weight, properties, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (f"r{i:04d}", source, target, rel_type, 1.0, "{}", now, now)
                for i, (source, target, rel_type) in enumerate(edges)
            ],
        )
    return storage


def _run(tmp_path: Path, body: Callable[[SQLiteBackend], Coroutine[Any, Any, None]], spec) -> None:
    """Build a fresh DB for one example, run *body* against it, tear it down."""
    node_count, edges = spec
    db_path = tmp_path / f"g{next(_db_counter):05d}.db"

    async def main() -> None:
        storage = await _build_graph(db_path, node_count, edges)
        try:
            await body(storage)
        finally:
            await storage.close()

    asyncio.run(main())


# ── Path validation ──────────────────────────────────────────────────────────


def _assert_path_is_real(
    path: list[dict[str, Any]],
    edge_set: set[Edge],
    *,
    origin: str,
    destination: str,
    depth: int,
) -> None:
    """A returned path must be walkable in the graph, step by step.

    Checks the three things a caller relies on: the path runs from the queried
    entity to the discovered one, its length matches the reported depth, and
    every consecutive pair really is joined by an edge of the stated type in
    the stated direction.
    """
    assert len(path) == depth + 1, f"path length {len(path)} != depth {depth} + 1"
    assert path[0]["entity_id"] == origin
    assert path[0]["relationship_type"] == ""
    assert path[0]["direction"] == ""
    assert path[-1]["entity_id"] == destination

    for previous, current in itertools.pairwise(path):
        before = previous["entity_id"]
        after = current["entity_id"]
        rel_type = current["relationship_type"]
        step_direction = current["direction"]
        if step_direction == "outgoing":
            assert (before, after, rel_type) in edge_set, (
                f"no outgoing edge {before} -[{rel_type}]-> {after}"
            )
        elif step_direction == "incoming":
            assert (after, before, rel_type) in edge_set, (
                f"no incoming edge {after} -[{rel_type}]-> {before}"
            )
        else:
            raise AssertionError(f"unexpected step direction {step_direction!r}")


# ── find_connections ─────────────────────────────────────────────────────────


@given(
    spec=graph_specs(),
    seed_index=st.integers(min_value=0, max_value=24),
    direction=st.sampled_from(DIRECTIONS),
    max_hops=st.integers(min_value=1, max_value=6),
    type_filter=st.one_of(
        st.none(),
        st.lists(st.sampled_from(REL_TYPES), min_size=1, max_size=3, unique=True),
    ),
)
@hyp_settings(max_examples=50)
def test_find_connections_matches_reference_bfs(
    tmp_path: Path,
    spec: tuple[int, list[Edge]],
    seed_index: int,
    direction: str,
    max_hops: int,
    type_filter: list[str] | None,
) -> None:
    """Reaches exactly the reference set, at exactly the reference depths."""
    node_count, edges = spec
    seed = _node_id(seed_index % node_count)
    edge_set = set(edges)

    adjacency = _reference_adjacency(edges, direction=direction, rel_types=type_filter)
    expected = _reference_depths(adjacency, [seed], max_hops)
    expected.pop(seed)  # the origin is never reported as one of its own connections

    async def body(storage: SQLiteBackend) -> None:
        traversal = GraphTraversal(storage)
        results = await traversal.find_connections(
            seed,
            max_hops=max_hops,
            direction=direction,
            relationship_types=type_filter,
        )

        actual = {str(item["entity"]["id"]): int(item["depth"]) for item in results}
        assert len(actual) == len(results), "an entity was reported more than once"
        assert actual == expected

        for item in results:
            _assert_path_is_real(
                item["path"],
                edge_set,
                origin=seed,
                destination=str(item["entity"]["id"]),
                depth=int(item["depth"]),
            )

    _run(tmp_path, body, spec)


# ── find_paths ───────────────────────────────────────────────────────────────


@given(
    spec=graph_specs(),
    source_index=st.integers(min_value=0, max_value=24),
    target_index=st.integers(min_value=0, max_value=24),
    max_hops=st.integers(min_value=1, max_value=6),
)
@hyp_settings(max_examples=50)
def test_find_paths_returns_only_minimal_valid_distinct_paths(
    tmp_path: Path,
    spec: tuple[int, list[Edge]],
    source_index: int,
    target_index: int,
    max_hops: int,
) -> None:
    """Empty exactly when unreachable; otherwise minimal, valid, and distinct."""
    node_count, edges = spec
    source = _node_id(source_index % node_count)
    target = _node_id(target_index % node_count)
    edge_set = set(edges)

    # find_paths always traverses in both directions and applies no type filter.
    adjacency = _reference_adjacency(edges, direction="both")
    expected_depths = _reference_depths(adjacency, [source], max_hops)
    expected_depth = None if source == target else expected_depths.get(target)

    async def body(storage: SQLiteBackend) -> None:
        traversal = GraphTraversal(storage)
        paths = await traversal.find_paths(source, target, max_hops=max_hops)

        if expected_depth is None:
            assert paths == [], "returned a path the reference says does not exist"
            return

        assert paths, f"{target} is reachable at depth {expected_depth} but no path was returned"
        assert len(paths) <= MAX_SHORTEST_PATHS

        seen: set[tuple[tuple[str, str, str], ...]] = set()
        for path in paths:
            _assert_path_is_real(
                path,
                edge_set,
                origin=source,
                destination=target,
                depth=expected_depth,
            )
            signature = tuple(
                (step["entity_id"], step["relationship_type"], step["direction"]) for step in path
            )
            assert signature not in seen, "duplicate path returned"
            seen.add(signature)

    _run(tmp_path, body, spec)


# ── get_subgraph ─────────────────────────────────────────────────────────────


@given(
    spec=graph_specs(),
    seed_indices=st.lists(st.integers(min_value=0, max_value=24), min_size=1, max_size=4),
    radius=st.integers(min_value=1, max_value=5),
)
@hyp_settings(max_examples=35)
def test_get_subgraph_is_exactly_reachable_and_has_no_dangling_edges(
    tmp_path: Path,
    spec: tuple[int, list[Edge]],
    seed_indices: list[int],
    radius: int,
) -> None:
    """Exactly the reachable set, and every edge has both endpoints in it."""
    node_count, edges = spec
    seeds = list(dict.fromkeys(_node_id(i % node_count) for i in seed_indices))

    adjacency = _reference_adjacency(edges, direction="both")
    expected_nodes = set(_reference_depths(adjacency, seeds, radius))
    expected_edges = {
        edge for edge in edges if edge[0] in expected_nodes and edge[1] in expected_nodes
    }

    async def body(storage: SQLiteBackend) -> None:
        traversal = GraphTraversal(storage)
        result = await traversal.get_subgraph(seeds, radius=radius)

        assert result["truncated"] is False  # default budget dwarfs a 25-node graph

        returned_nodes = {str(e["id"]) for e in result["entities"]}
        assert len(returned_nodes) == len(result["entities"]), "an entity was returned twice"
        assert returned_nodes == expected_nodes

        returned_edges = set()
        for rel in result["relationships"]:
            source = str(rel["source_id"])
            target = str(rel["target_id"])
            # No dangling edges: both endpoints must be in the returned set.
            assert source in returned_nodes, f"edge source {source} missing from entities"
            assert target in returned_nodes, f"edge target {target} missing from entities"
            returned_edges.add((source, target, str(rel["relationship_type"])))

        assert returned_edges == expected_edges

    _run(tmp_path, body, spec)


# ── Node budget ──────────────────────────────────────────────────────────────


@given(
    spec=graph_specs(),
    seed_index=st.integers(min_value=0, max_value=24),
    budget=st.integers(min_value=1, max_value=6),
)
@hyp_settings(max_examples=35)
def test_node_budget_truncates_to_a_subset_and_says_so(
    tmp_path: Path,
    spec: tuple[int, list[Edge]],
    seed_index: int,
    budget: int,
) -> None:
    """A budgeted traversal never invents nodes, and admits when it cut short.

    Three claims, in increasing strength:

    * the returned set is always a subset of what is truly reachable;
    * more reachable nodes than budget implies ``truncated``;
    * ``truncated`` false implies the result is complete, not merely a subset.
    """
    node_count, edges = spec
    seed = _node_id(seed_index % node_count)
    radius = 5

    adjacency = _reference_adjacency(edges, direction="both")
    expected_nodes = set(_reference_depths(adjacency, [seed], radius))

    async def body(storage: SQLiteBackend) -> None:
        traversal = GraphTraversal(storage, node_budget=budget)
        result = await traversal.get_subgraph([seed], radius=radius)
        returned = {str(e["id"]) for e in result["entities"]}

        assert returned <= expected_nodes, "returned an entity that is not reachable at all"
        assert len(returned) <= max(budget, 1)

        if len(expected_nodes) > budget:
            assert result["truncated"] is True, "exceeded the budget without reporting truncation"
        if result["truncated"] is False:
            assert returned == expected_nodes, "reported a complete result that is incomplete"

    _run(tmp_path, body, spec)
