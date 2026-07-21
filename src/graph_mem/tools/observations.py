"""Observation tools — add, update, delete."""

from __future__ import annotations

from typing import Any

from graph_mem.models import Observation
from graph_mem.utils import GraphMemError

from ._core import (
    _embed_observation_texts,
    _embed_observations,
    _error_response,
    _require_state,
    _require_text,
    _require_text_list,
    tool,
)


@tool()
async def add_observations(
    entity_name: str,
    observations: list[str],
    source: str = "",
) -> dict[str, Any]:
    """Add factual observations to an existing entity.

    Observations are atomic statements about an entity (facts, quotes, events).
    They are embedded separately for fine-grained semantic search.

    Args:
        entity_name: Name of the entity to attach observations to.
        observations: List of observation text strings.
        source: Optional provenance string (e.g. session ID, document name).
    """
    try:
        state = _require_state()
        entity_name = _require_text(entity_name, "entity_name")
        observations = _require_text_list(observations, "observations")
        source = _require_text(source, "source", allow_empty=True)

        obs_objs = [Observation.pending(text, source=source) for text in observations]

        results = await state.graph.add_observations(entity_name, obs_objs)
        await _embed_observations(results)

        return {"results": results, "count": len(results)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_observations")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="add_observations")


@tool()
async def delete_observations(
    entity_name: str,
    observation_ids: list[str],
) -> dict[str, Any]:
    """Remove specific observations from an entity by observation ID.

    Validates that the observations belong to the specified entity before
    deleting. Also cleans up associated embeddings.

    Args:
        entity_name: Name of the entity the observations belong to.
        observation_ids: List of observation IDs to delete.
    """
    try:
        state = _require_state()
        entity_name = _require_text(entity_name, "entity_name")
        observation_ids = _require_text_list(observation_ids, "observation_ids")

        deleted = await state.graph.delete_observations(entity_name, observation_ids)

        return {
            "entity_name": entity_name,
            "deleted": deleted,
            "requested": len(observation_ids),
            "status": "deleted" if deleted > 0 else "not_found",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_observations")


@tool()
async def update_observation(
    entity_name: str,
    observation_id: str,
    content: str,
) -> dict[str, Any]:
    """Update the text content of an existing observation in-place.

    Modifies the observation content directly — does not delete and re-create.
    Recomputes the embedding for the updated content automatically.

    Args:
        entity_name: Name of the entity the observation belongs to.
        observation_id: ID of the observation to update.
        content: New text content for the observation.
    """
    try:
        state = _require_state()
        entity_name = _require_text(entity_name, "entity_name")
        observation_id = _require_text(observation_id, "observation_id")
        content = _require_text(content, "content")

        result = await state.graph.update_observation(entity_name, observation_id, content)

        # Recompute embedding for updated content
        await _embed_observation_texts([(observation_id, content)])

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_observation")
