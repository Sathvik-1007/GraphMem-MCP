"""Graph traversal — breadth-first search and path-finding over the knowledge graph.

Responsible for topology questions: what is reachable from here, how do these
two entities connect, what does the neighbourhood around these seeds look like.
Not responsible for ranking (see :mod:`graph_mem.semantic.search`) or for
mutating the graph (see :mod:`graph_mem.graph.engine`).

Why this is Python and not a recursive CTE
------------------------------------------
The obvious SQL formulation — a ``WITH RECURSIVE`` walk carrying a per-row
``visited`` array — does not perform breadth-first search.  It enumerates every
*simple path*, because each row's visited set is private to that path and
therefore cannot stop a node from being re-expanded along a different route.
On a 14-node, 91-edge graph at ``max_hops=6`` that formulation materialised
1 409 006 intermediate rows and took 30 seconds to return 13 entities.

A genuine BFS keeps one *global* visited set, so every node is expanded at most
once.  SQLite's recursive CTE has no way to express that: the recursive term
cannot query the rows the CTE has produced so far.  Doing the level-stepping
here instead costs one query per hop — at most ten, each an indexed lookup over
the current frontier — and makes the work linear in the nodes and edges
actually visited.

Every traversal is additionally bounded by a node budget, so even a fully
connected graph returns promptly and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from graph_mem.models.entity import Entity
from graph_mem.models.relationship import Relationship
from graph_mem.utils.errors import ValidationError
from graph_mem.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from graph_mem.storage.base import StorageBackend

log = get_logger("graph.traversal")

# Ceiling on traversal depth.  Beyond ten hops a "connection" in a knowledge
# graph carries no useful meaning — nearly everything is reachable — and the
# result stops being something an agent can act on.
MAX_HOPS_LIMIT = 10

# Ceiling on subgraph expansion.  Lower than MAX_HOPS_LIMIT because a subgraph
# returns full entity and relationship records rather than a summary, so its
# response grows much faster per hop.
MAX_RADIUS_LIMIT = 5

# Default cap on distinct entities visited per traversal.  Overridden from
# ``Config.traversal_node_budget``; kept here so GraphTraversal is usable
# standalone.
DEFAULT_NODE_BUDGET = 5000

# How many distinct shortest paths ``find_paths`` will enumerate.  All returned
# paths have the same (minimal) length; this bounds the fan-out when many
# equally-short routes exist.
MAX_SHORTEST_PATHS = 10

# Accepted values for the ``direction`` argument.
_VALID_DIRECTIONS = ("outgoing", "incoming", "both")


@dataclass(frozen=True, slots=True)
class _Step:
    """How the traversal arrived at a node: one edge, and where it came from."""

    parent: str
    relationship_type: str
    direction: str


@dataclass(slots=True)
class _BfsResult:
    """Outcome of one breadth-first expansion.

    Attributes:
        depth: Hop count from the nearest seed, for every node reached.
            Seeds are at depth 0.
        parents: For each non-seed node, every edge that reaches it at its
            *minimal* depth. More than one entry means several equally-short
            routes exist. Deeper routes are not recorded — they can only
            produce longer paths.
        truncated: Whether the node budget stopped the expansion early, so the
            result is a subset of what is reachable.
    """

    depth: dict[str, int] = field(default_factory=dict)
    parents: dict[str, list[_Step]] = field(default_factory=dict)
    truncated: bool = False


class GraphTraversal:
    """Breadth-first traversal over the knowledge graph.

    All methods are async and read-only.

    Args:
        storage: Backend supplying batched adjacency lookups.
        node_budget: Maximum distinct entities any single traversal may visit.
            Results that hit the budget are marked ``truncated`` rather than
            silently cut short.
    """

    def __init__(
        self,
        storage: StorageBackend,
        *,
        node_budget: int = DEFAULT_NODE_BUDGET,
    ) -> None:
        if node_budget < 1:
            raise ValueError(f"node_budget must be >= 1, got {node_budget}")
        self._storage = storage
        self._node_budget = node_budget

    # ── Core algorithm ───────────────────────────────────────────────────

    async def _bfs(
        self,
        seed_ids: Sequence[str],
        *,
        max_hops: int,
        direction: str = "both",
        relationship_types: list[str] | None = None,
        stop_at: str | None = None,
    ) -> _BfsResult:
        """Expand outward from *seed_ids* one hop at a time.

        Args:
            seed_ids: Starting entities, all placed at depth 0.
            max_hops: How many levels to expand. Already clamped by callers.
            direction: Edge orientation to follow, relative to the node being
                expanded.
            relationship_types: Optional edge-type whitelist.
            stop_at: Stop as soon as this entity is reached. Because expansion
                is strictly level-by-level, the depth recorded for it when the
                loop stops is already minimal.

        Returns:
            A :class:`_BfsResult` whose ``depth`` includes the seeds.

        Performance:
            ``O(V + E)`` over the visited subgraph, in at most *max_hops*
            round trips. Each round trip is one indexed adjacency query per
            900-id chunk of the frontier.
        """
        result = _BfsResult()
        frontier: list[str] = []
        for seed in seed_ids:
            if seed not in result.depth:
                result.depth[seed] = 0
                frontier.append(seed)

        if not frontier:
            return result

        for hop in range(1, max_hops + 1):
            if not frontier:
                break

            edges = await self._storage.fetch_adjacent_edges(
                frontier,
                direction=direction,
                relationship_types=relationship_types,
            )
            if not edges:
                break

            in_frontier = set(frontier)
            next_frontier: list[str] = []

            for source_id, target_id, rel_type in edges:
                # An edge is usable from whichever endpoint is on the frontier.
                # When both are, it yields a step in each orientation, which is
                # how sibling nodes at the same depth get their alternate
                # shortest-path parents recorded.
                for parent, neighbour, step_direction in _orientations(
                    source_id, target_id, in_frontier, direction
                ):
                    if neighbour == parent:
                        continue  # self-loop: reaches nothing new
                    known_depth = result.depth.get(neighbour)

                    if known_depth is None:
                        if len(result.depth) >= self._node_budget:
                            result.truncated = True
                            continue
                        result.depth[neighbour] = hop
                        next_frontier.append(neighbour)
                        known_depth = hop

                    # Record the edge only when it is one of the shortest ways
                    # in.  A deeper arrival can never start a shorter path, and
                    # keeping those is exactly what makes path enumeration
                    # exponential.
                    if known_depth == hop:
                        result.parents.setdefault(neighbour, []).append(
                            _Step(parent, rel_type, step_direction)
                        )

            frontier = next_frontier

            if stop_at is not None and stop_at in result.depth:
                break

        if result.truncated:
            log.warning(
                "Traversal hit the %d-entity budget and returned a partial result",
                self._node_budget,
            )
        return result

    # ── Connection discovery ─────────────────────────────────────────────

    async def find_connections(
        self,
        entity_id: str,
        *,
        max_hops: int = 3,
        relationship_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Find all entities reachable within *max_hops* from *entity_id*.

        Args:
            entity_id: Starting entity's ID.
            max_hops: Maximum traversal depth (1-10, clamped).
            relationship_types: Optional whitelist of relationship types.
            direction: ``"outgoing"``, ``"incoming"``, or ``"both"``.

        Returns:
            List of dicts ordered by depth then name, each containing:
            - ``entity``: The discovered entity as a dict.
            - ``depth``: Hop count from the starting entity.
            - ``path``: One shortest route in, as a list of
              ``{entity_id, entity_name, relationship_type, direction}`` steps.

        Raises:
            ValidationError: *direction* is not a recognised value.
        """
        _validate_direction(direction)
        max_hops = _clamp(max_hops, 1, MAX_HOPS_LIMIT)

        bfs = await self._bfs(
            [entity_id],
            max_hops=max_hops,
            direction=direction,
            relationship_types=relationship_types,
        )

        reached = [eid for eid, depth in bfs.depth.items() if depth > 0]
        if not reached:
            return []

        # One batched fetch for the rows and one for the names used in paths,
        # instead of a lookup per discovered entity.
        entity_rows = await self._storage.fetch_entity_rows(reached)
        rows_by_id = {str(row["id"]): row for row in entity_rows}

        paths = {eid: _shortest_path_steps(eid, bfs) for eid in reached}
        path_node_ids = {step.parent for steps in paths.values() for step in steps}
        path_node_ids.update(reached)
        name_map = await self._storage.resolve_entity_names(path_node_ids)

        results: list[dict[str, Any]] = []
        for eid in reached:
            row = rows_by_id.get(eid)
            if row is None:
                # Edge referencing a deleted entity — skip rather than emit a
                # half-populated record the caller cannot act on.
                log.debug("Skipping %s: reachable by edge but no entity row", eid)
                continue
            results.append(
                {
                    "entity": Entity.from_row(row).to_dict(),
                    "depth": bfs.depth[eid],
                    "path": _render_path(eid, paths[eid], name_map),
                }
            )

        results.sort(key=lambda item: (item["depth"], str(item["entity"].get("name", ""))))
        log.debug(
            "find_connections from %s: %d entities within %d hops",
            entity_id,
            len(results),
            max_hops,
        )
        return results

    # ── Shortest paths ───────────────────────────────────────────────────

    async def find_paths(
        self,
        source_id: str,
        target_id: str,
        *,
        max_hops: int = 5,
    ) -> list[list[dict[str, Any]]]:
        """Find the shortest paths between two entities.

        Args:
            source_id: Starting entity ID.
            target_id: Destination entity ID.
            max_hops: Maximum path length (1-10, clamped).

        Returns:
            Up to :data:`MAX_SHORTEST_PATHS` paths, all of the same minimal
            length. Each path runs source-first and includes both endpoints;
            each step is ``{entity_id, entity_name, relationship_type}``, where
            the relationship type is the edge *entering* that step's entity and
            is empty for the source. Empty when no path exists within
            *max_hops*, or when source and target are the same entity.
        """
        max_hops = _clamp(max_hops, 1, MAX_HOPS_LIMIT)

        if source_id == target_id:
            return []

        bfs = await self._bfs([source_id], max_hops=max_hops, stop_at=target_id)
        if target_id not in bfs.depth:
            return []

        step_paths = _enumerate_shortest_paths(target_id, bfs, MAX_SHORTEST_PATHS)

        node_ids = {source_id, target_id}
        for steps in step_paths:
            node_ids.update(step.parent for step in steps)
        name_map = await self._storage.resolve_entity_names(node_ids)

        results = [_render_path(target_id, steps, name_map) for steps in step_paths]
        log.debug("find_paths %s -> %s: %d paths", source_id, target_id, len(results))
        return results

    # ── Subgraph extraction ──────────────────────────────────────────────

    async def get_subgraph(
        self,
        entity_ids: list[str],
        *,
        radius: int = 2,
    ) -> dict[str, Any]:
        """Extract the subgraph around a set of seed entities.

        Args:
            entity_ids: Seed entity IDs.
            radius: How many hops to expand from the seeds (1-5, clamped).

        Returns:
            ``{entities: [...], relationships: [...], truncated: bool}``.
            ``truncated`` is ``True`` when the node budget cut the expansion
            short, so the caller can tell a complete neighbourhood from a
            partial one.

        Performance:
            A single multi-source breadth-first expansion, not one per seed.
            Seeds that reach the same region share the work instead of
            repeating it.
        """
        radius = _clamp(radius, 1, MAX_RADIUS_LIMIT)

        if not entity_ids:
            return {"entities": [], "relationships": [], "truncated": False}

        bfs = await self._bfs(entity_ids, max_hops=radius)
        all_entity_ids = list(bfs.depth)

        if not all_entity_ids:
            return {"entities": [], "relationships": [], "truncated": bfs.truncated}

        entity_rows = await self._storage.fetch_entity_rows(all_entity_ids)
        entities = [Entity.from_row(row).to_dict() for row in entity_rows]

        rel_rows = await self._storage.fetch_relationships_between(all_entity_ids)
        relationships = [Relationship.from_row(row).to_dict() for row in rel_rows]

        log.debug(
            "get_subgraph: %d seeds, radius=%d -> %d entities, %d relationships",
            len(entity_ids),
            radius,
            len(entities),
            len(relationships),
        )
        return {
            "entities": entities,
            "relationships": relationships,
            "truncated": bfs.truncated,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: int, low: int, high: int) -> int:
    """Constrain *value* to the inclusive range ``[low, high]``."""
    return max(low, min(value, high))


def _validate_direction(direction: str) -> None:
    """Reject a direction the traversal cannot honour.

    Silently treating an unrecognised value as ``"both"`` returns plausible
    results for a query the caller did not ask, which is worse than an error.
    """
    if direction not in _VALID_DIRECTIONS:
        raise ValidationError(
            f"direction must be one of {', '.join(_VALID_DIRECTIONS)}, got {direction!r}"
        )


def _orientations(
    source_id: str,
    target_id: str,
    frontier: set[str],
    direction: str,
) -> Iterable[tuple[str, str, str]]:
    """Yield ``(parent, neighbour, direction)`` for each usable end of an edge.

    An edge reaches the frontier from its source, its target, or both. Which
    ends count depends on the requested direction: following ``"outgoing"``
    edges means only the source end may act as a parent.
    """
    if direction in ("outgoing", "both") and source_id in frontier:
        yield source_id, target_id, "outgoing"
    if direction in ("incoming", "both") and target_id in frontier:
        yield target_id, source_id, "incoming"


def _shortest_path_steps(node_id: str, bfs: _BfsResult) -> list[_Step]:
    """Walk parent links back to a seed, returning the steps source-first.

    Any single chain through ``parents`` is a shortest path, because only
    minimal-depth arrivals were recorded.
    """
    steps: list[_Step] = []
    current = node_id
    # Bounded by the node's depth: each hop strictly decreases it.
    while True:
        parent_steps = bfs.parents.get(current)
        if not parent_steps:
            break
        step = parent_steps[0]
        steps.append(step)
        current = step.parent
    steps.reverse()
    return steps


def _enumerate_shortest_paths(
    target_id: str,
    bfs: _BfsResult,
    limit: int,
) -> list[list[_Step]]:
    """Enumerate up to *limit* distinct shortest paths ending at *target_id*.

    Walks the parent DAG backwards.  Because it only ever contains
    minimal-depth edges it is acyclic and every root-to-target chain has the
    same length, so a depth-first walk with an early cut-off is bounded by
    *limit* paths rather than by the number of paths in the graph.
    """
    paths: list[list[_Step]] = []

    def walk(node: str, suffix: list[_Step]) -> None:
        if len(paths) >= limit:
            return
        parent_steps = bfs.parents.get(node)
        if not parent_steps:
            paths.append(list(reversed(suffix)))
            return
        for step in parent_steps:
            if len(paths) >= limit:
                return
            walk(step.parent, [*suffix, step])

    walk(target_id, [])
    return paths


def _render_path(
    node_id: str,
    steps: list[_Step],
    name_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Turn parent links into the caller-facing step list.

    The result runs source-first and includes both endpoints. Each entry names
    an entity and the relationship that leads *into* it, so the first entry —
    the origin — has an empty relationship type.
    """
    if not steps:
        return []

    origin = steps[0].parent
    rendered: list[dict[str, Any]] = [
        {
            "entity_id": origin,
            "entity_name": name_map.get(origin, ""),
            "relationship_type": "",
            "direction": "",
        }
    ]
    for index, step in enumerate(steps):
        # Each step's parent is the previous step's arrival point, so the
        # entity this step arrives at is the next parent, or the destination.
        arrival = steps[index + 1].parent if index + 1 < len(steps) else node_id
        rendered.append(
            {
                "entity_id": arrival,
                "entity_name": name_map.get(arrival, ""),
                "relationship_type": step.relationship_type,
                "direction": step.direction,
            }
        )
    return rendered
