"""Maintenance tools — graph_health, compact_observations, suggest_connections, audit_graph."""

from __future__ import annotations

import contextlib
import json as _json
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from graph_mem.graph.engine import ObservationResult

from graph_mem.models import Observation
from graph_mem.utils import GraphMemError

from ._core import (
    MAX_SEARCH_LIMIT,
    _clamp_limit,
    _embed_observations,
    _error_response,
    _require_state,
    _require_text,
    _require_text_list,
    tool,
)

#: How many entities one ``audit_graph`` run inspects.  The audit reads every
#: entity row into memory to cross-check it against four separate checks, so it
#: is the one tool whose cost grows with the whole graph rather than with the
#: caller's request.  2000 entities is roughly four times the size at which
#: ``graph_health`` already starts advising the user to prune, and the response
#: says when the scan was cut short.
AUDIT_ENTITY_SCAN_LIMIT = 2000

#: How many entity names each audit category lists by name.  The counts are
#: always exact; only the enumeration is trimmed.
AUDIT_NAMES_PER_CATEGORY = 30


class _Hotspot(TypedDict):
    """One entity carrying an unusually large number of observations.

    A plain dict of mixed str/int values degrades to ``dict[str, object]``,
    which makes the numeric comparison below untypeable.
    """

    name: str
    entity_type: str
    observation_count: int


@tool()
async def graph_health() -> dict[str, Any]:
    """Ask "does this graph need cleaning?" — returns maintenance signals and a
    short list of suggested actions, at fixed cost regardless of graph size.

    Sits between the other two overview tools: read_graph describes the graph's
    shape and says nothing about quality; audit_graph names every problem
    entity and scans the whole graph to do it. This one reports counts, the top
    5 observation hotspots, up to 10 entities missing descriptions, the top 10
    entity types, and what to do about them.
    """
    try:
        state = _require_state()
        storage = state.storage

        entity_count = await storage.count_entities()
        rel_count = await storage.count_relationships()
        obs_count = await storage.count_observations()
        type_dist = await storage.entity_type_distribution()

        # Cap type distribution to top 10
        sorted_types = sorted(type_dist.items(), key=lambda x: x[1], reverse=True)
        type_dist_capped = dict(sorted_types[:10])

        # Top 5 observation hotspots — entities with most observations
        hotspot_rows = await storage.fetch_all(
            """
            SELECT e.id, e.name, e.entity_type, COUNT(o.id) AS obs_count
            FROM entities e
            JOIN observations o ON o.entity_id = e.id
            GROUP BY e.id
            ORDER BY obs_count DESC
            LIMIT 5
            """,
        )
        hotspots: list[_Hotspot] = [
            {
                "name": str(r["name"]),
                "entity_type": str(r["entity_type"]),
                "observation_count": int(r["obs_count"]),
            }
            for r in hotspot_rows
        ]

        # Entities missing descriptions (top 10)
        no_desc_rows = await storage.fetch_all(
            """
            SELECT name, entity_type
            FROM entities
            WHERE description IS NULL OR description = ''
            ORDER BY updated_at DESC
            LIMIT 10
            """,
        )
        missing_descriptions = [
            {"name": str(r["name"]), "entity_type": str(r["entity_type"])} for r in no_desc_rows
        ]

        # Count entities with empty descriptions
        no_desc_count_row = await storage.fetch_one(
            "SELECT COUNT(*) AS cnt FROM entities WHERE description IS NULL OR description = ''",
        )
        no_desc_count = int(no_desc_count_row["cnt"]) if no_desc_count_row else 0

        # Build suggested actions
        actions: list[str] = []
        if entity_count > 500:
            actions.append(
                f"Graph has {entity_count} entities — consider pruning stale or low-value entries"
            )
        if hotspots and hotspots[0]["observation_count"] > 15:
            actions.append(
                f"Entity '{hotspots[0]['name']}' has {hotspots[0]['observation_count']} "
                f"observations — consider compacting with compact_observations"
            )
        if no_desc_count > 0:
            actions.append(
                f"{no_desc_count} entities have empty descriptions — use update_entity to add them"
            )
        if obs_count > 0 and entity_count > 0:
            avg_obs = obs_count / entity_count
            if avg_obs > 10:
                actions.append(
                    f"Average {avg_obs:.1f} observations per entity — consider consolidating"
                )

        return {
            "counts": {
                "entities": entity_count,
                "relationships": rel_count,
                "observations": obs_count,
            },
            "entity_types": type_dist_capped,
            "observation_hotspots": hotspots,
            "missing_descriptions": missing_descriptions,
            "missing_description_count": no_desc_count,
            "suggested_actions": actions,
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="graph_health")


@tool()
async def compact_observations(
    entity_name: str,
    keep_ids: list[str],
    new_observations: list[str],
) -> dict[str, Any]:
    """Atomic observation compaction — delete old observations and add new ones in one step.

    Use this to consolidate many observations into fewer, denser ones.
    You decide what to merge — this tool makes it atomic so no data is lost.

    Workflow: get_entity → read observations → decide which to keep vs merge →
    call compact_observations with keep_ids (observations to preserve unchanged)
    and new_observations (your merged/summarized replacements).

    Args:
        entity_name: Name of the entity to compact.
        keep_ids: Observation IDs to preserve unchanged. All others are deleted.
            Duplicates are counted once.
        new_observations: New observation texts to add (your merged summaries).
    """
    try:
        state = _require_state()
        entity_name = _require_text(entity_name, "entity_name")
        keep_ids = _require_text_list(keep_ids, "keep_ids")
        new_observations = _require_text_list(new_observations, "new_observations")

        # Resolve entity and get current observations
        entity = await state.graph.resolve_entity(entity_name)
        current_obs = await state.storage.get_observations_for_entity(entity.id)

        # Determine which IDs to delete (everything not in keep_ids)
        keep_set = set(keep_ids)
        delete_ids = [str(obs["id"]) for obs in current_obs if str(obs["id"]) not in keep_set]

        # Validate keep_ids all belong to this entity
        current_ids = {str(obs["id"]) for obs in current_obs}
        invalid_keeps = keep_set - current_ids
        if invalid_keeps:
            return _error_response(
                GraphMemError(
                    f"Observation IDs not found on entity '{entity_name}': "
                    f"{', '.join(sorted(invalid_keeps))}"
                ),
                tool_name="compact_observations",
            )

        # Wrap delete+add in a transaction so partial failure doesn't lose data
        deleted_count = 0
        added_results: list[ObservationResult] = []
        async with state.storage.transaction():
            for obs_id in delete_ids:
                was_deleted = await state.storage.delete_observation(obs_id)
                if was_deleted:
                    deleted_count += 1
                    # Clean up embedding
                    if state.embeddings.available:
                        with contextlib.suppress(GraphMemError):
                            await state.embeddings.delete_observation_embedding(obs_id)

            # Add new observations
            if new_observations:
                obs_objs = [Observation.pending(text) for text in new_observations]
                added_results = await state.graph.add_observations(entity_name, obs_objs)

        # Embed after the transaction commits.  A cold embedding model takes
        # 30+ seconds to load, and holding a write transaction for that long
        # blocks every other tool call.  The rows are already durable; a failure
        # here costs the new observations their vectors, not their existence.
        if added_results:
            await _embed_observations(added_results)

        # keep_ids may repeat an ID; the observation is still kept only once.
        kept_count = len(keep_set)

        return {
            "entity_name": entity_name,
            "before": len(current_obs),
            "deleted": deleted_count,
            "kept": kept_count,
            "added": len(added_results),
            "after": kept_count + len(added_results),
            "status": "compacted",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="compact_observations")


@tool()
async def suggest_connections(
    entity_name: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Find potential relationships for an entity by semantic similarity.

    Solves the "large graph" problem: when you add a new entity, you can't
    read the whole graph to know what to connect it to. This tool searches
    for semantically related entities and shows which ones already have
    relationships vs which are unconnected — giving you concrete suggestions.

    Args:
        entity_name: Name of the entity to find connections for.
        limit: Max suggestions to return (default 10, clamped to 1-100).
    """
    try:
        state = _require_state()
        entity_name = _require_text(entity_name, "entity_name")
        limit = _clamp_limit(limit, maximum=MAX_SEARCH_LIMIT)

        # Resolve the entity
        entity = await state.graph.resolve_entity(entity_name)

        # Search for semantically similar entities using the entity's own text
        query = f"{entity.name} {entity.entity_type} {entity.description or ''}"
        search_results = await state.search.search_entities(
            query.strip(),
            limit=limit + 1,  # +1 because self might appear
            include_observations=False,
            boost_from_observations=False,  # structural similarity only
        )

        # Get existing relationships for this entity
        existing_rels = await state.graph.get_relationships(entity_name)
        connected_names: set[str] = set()
        for rel in existing_rels:
            connected_names.add(str(rel.get("source_name", "")))
            connected_names.add(str(rel.get("target_name", "")))
        connected_names.discard("")
        connected_names.discard(entity_name)

        # Build suggestions
        suggestions: list[dict[str, Any]] = []
        for result in search_results:
            name = str(result.get("name", ""))
            if name == entity_name:
                continue  # skip self

            already_connected = name in connected_names
            suggestions.append(
                {
                    "name": name,
                    "entity_type": result.get("entity_type", ""),
                    "description": result.get("description", ""),
                    "already_connected": already_connected,
                    "score": result.get("score", 0),
                }
            )

            if len(suggestions) >= limit:
                break

        unconnected = [s for s in suggestions if not s["already_connected"]]
        connected = [s for s in suggestions if s["already_connected"]]

        return {
            "entity": entity_name,
            "suggestions": suggestions,
            "unconnected_count": len(unconnected),
            "already_connected_count": len(connected),
            "hint": (
                "Entities listed as already_connected=false are candidates "
                "for new relationships. Use add_relationships to connect them."
                if unconnected
                else "All similar entities are already connected."
            ),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="suggest_connections")


@tool()
async def audit_graph() -> dict[str, Any]:
    """Name every entity with a data-quality problem — the thorough, expensive
    check to run after a bulk import or before a cleanup pass.

    Unlike read_graph (shape only) and graph_health (fixed-cost signals plus
    advice), this scans up to 2000 entities and lists the offenders in five
    categories: disconnected, missing description, missing observations, empty
    properties, and only-one-relationship.

    Returns a dict; ``report`` holds the same findings rendered as plain text
    for direct reading, and ``truncated`` says whether the graph outgrew the
    scan limit.
    """
    try:
        state = _require_state()
        storage = state.storage

        entity_total = await storage.count_entities()
        rel_total = await storage.count_relationships()
        obs_total = await storage.count_observations()

        # One bounded pass carries every per-entity number the audit needs.
        # It replaces three unbounded scans: the entity list, a DISTINCT scan
        # of relationship endpoints, and a GROUP BY of relationships per
        # entity.  The last two answered the same question twice — "is this
        # entity connected" is just "is its edge count zero".
        rows = await storage.fetch_all(
            """
            SELECT e.id, e.name, e.entity_type, e.description, e.properties,
                   (SELECT COUNT(*) FROM relationships r
                     WHERE r.source_id = e.id OR r.target_id = e.id) AS rel_count,
                   (SELECT COUNT(*) FROM observations o
                     WHERE o.entity_id = e.id) AS obs_count
            FROM entities e
            ORDER BY e.entity_type, e.name
            LIMIT ?
            """,
            (AUDIT_ENTITY_SCAN_LIMIT,),
        )

        counts = {"entities": entity_total, "relationships": rel_total, "observations": obs_total}
        if not rows:
            return {
                "report": "AUDIT: Graph is empty — no entities to audit.",
                "counts": counts,
                "scanned_entities": 0,
                "truncated": False,
                "quality_score": 100.0,
                "issues": {},
                "actions": [],
            }

        scanned = len(rows)
        truncated = entity_total > scanned

        # ── Categories ───────────────────────────────────────────────────
        disconnected = [e for e in rows if int(e["rel_count"]) == 0]
        no_desc = [e for e in rows if not e["description"] or not str(e["description"]).strip()]
        no_obs = [e for e in rows if int(e["obs_count"]) == 0]

        no_props = []
        for e in rows:
            props_raw = e.get("properties", "{}")
            try:
                props = _json.loads(props_raw) if isinstance(props_raw, str) else props_raw
            except (ValueError, TypeError):
                props = {}
            if not props:
                no_props.append(e)

        low_conn = [e for e in rows if int(e["rel_count"]) == 1]

        # ── Build plain-text report ──────────────────────────────────────
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("GRAPH AUDIT REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Entities: {entity_total}")
        lines.append(f"Relationships: {rel_total}")
        lines.append(f"Observations: {obs_total}")
        if truncated:
            lines.append(f"NOTE: audited the first {scanned} entities of {entity_total}.")
        lines.append("")

        # Score
        issues_total = len(disconnected) + len(no_desc) + len(no_obs) + len(no_props)
        max_issues = scanned * 4  # 4 checks per entity
        quality_pct = round(100 * (1 - issues_total / max_issues), 1) if max_issues > 0 else 100.0
        lines.append(
            f"Quality Score: {quality_pct}% ({issues_total} issues across {scanned} entities)"
        )
        lines.append("")

        def section(title: str, offenders: list[dict[str, Any]], blurb: str, clean: str) -> None:
            """Append one category block: header, explanation, capped name list."""
            lines.append("-" * 60)
            lines.append(f"{title} ({len(offenders)}/{scanned})")
            lines.append(f"  {blurb}")
            if offenders:
                for e in offenders[:AUDIT_NAMES_PER_CATEGORY]:
                    lines.append(f"  - {e['name']} [{e['entity_type']}]")
                if len(offenders) > AUDIT_NAMES_PER_CATEGORY:
                    lines.append(f"  ... and {len(offenders) - AUDIT_NAMES_PER_CATEGORY} more")
            else:
                lines.append(f"  {clean}")
            lines.append("")

        section(
            "DISCONNECTED ENTITIES",
            disconnected,
            "Entities with zero relationships — isolated nodes.",
            "None — all entities are connected.",
        )
        section(
            "MISSING DESCRIPTIONS",
            no_desc,
            "Entities with empty or missing description field.",
            "None — all entities have descriptions.",
        )
        section(
            "MISSING OBSERVATIONS",
            no_obs,
            "Entities with zero observations — no factual detail stored.",
            "None — all entities have observations.",
        )
        section(
            "EMPTY PROPERTIES",
            no_props,
            "Entities with no key-value properties set.",
            "None — all entities have properties.",
        )
        section(
            "LOW-CONNECTION ENTITIES",
            low_conn,
            "Connected entities with only 1 relationship — weakly linked.",
            "None — all connected entities have 2+ relationships.",
        )

        # ── Summary ──────────────────────────────────────────────────────
        actions: list[str] = []
        if disconnected:
            actions.append(f"Connect {len(disconnected)} isolated entities with add_relationships")
        if no_desc:
            actions.append(f"Add descriptions to {len(no_desc)} entities with update_entity")
        if no_obs:
            actions.append(f"Add observations to {len(no_obs)} entities with add_observations")
        if no_props:
            actions.append(f"Add properties to {len(no_props)} entities with update_entity")
        if low_conn:
            actions.append(
                f"Strengthen {len(low_conn)} weakly-linked entities with more relationships"
            )

        lines.append("=" * 60)
        lines.append("ACTIONS")
        if actions:
            for number, action in enumerate(actions, start=1):
                lines.append(f"  {number}. {action}")
        else:
            lines.append("  Graph looks clean — no issues found.")
        lines.append("=" * 60)

        return {
            "report": "\n".join(lines),
            "counts": counts,
            "scanned_entities": scanned,
            "truncated": truncated,
            "quality_score": quality_pct,
            "issues": {
                "disconnected": len(disconnected),
                "missing_descriptions": len(no_desc),
                "missing_observations": len(no_obs),
                "empty_properties": len(no_props),
                "low_connection": len(low_conn),
            },
            "actions": actions,
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="audit_graph")
