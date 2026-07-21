import { useReducer, useEffect, useCallback, useMemo } from "react";
import { fetchGraph, fetchEntity, fetchStats } from "../api/client";
import type { AppState, AppAction } from "../types/graph";

/**
 * How many entities to pull into the visualization.
 *
 * The API defaults to 500 and hard-caps at 5000. The old value of 200 silently
 * truncated any real graph — and since the API only returns relationships
 * *between* the entities it returned, a truncated graph also under-reports
 * connectivity. 2000 covers the large majority of graphs while keeping the
 * payload and the per-frame node count sane; anything beyond it is reported to
 * the user via `truncated` below rather than being quietly dropped.
 */
const GRAPH_FETCH_LIMIT = 2000;

const initialState: AppState = {
  graph: null,
  selectedEntity: null,
  stats: null,
  searchResults: null,
  visibleEntityTypes: new Set(),
  knownEntityTypes: new Set(),
  showEdgeLabels: false,
  loading: true,
  error: null,
};

function sameMembers(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

function reducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "SET_GRAPH": {
      // A refresh must not wipe the user's type filters. Types the user has
      // explicitly hidden stay hidden; types never seen before default to
      // visible; types that no longer appear in the graph drop out.
      const present = new Set(action.payload.entities.map((e) => e.entity_type));
      const visible = new Set<string>();
      for (const t of present) {
        if (!state.knownEntityTypes.has(t) || state.visibleEntityTypes.has(t)) {
          visible.add(t);
        }
      }
      const known = new Set(state.knownEntityTypes);
      for (const t of present) known.add(t);

      return {
        ...state,
        graph: action.payload,
        // Reuse the old Set when the contents are unchanged: a fresh identity
        // would retrigger GraphCanvas's build effect and rescramble the layout.
        visibleEntityTypes: sameMembers(visible, state.visibleEntityTypes)
          ? state.visibleEntityTypes
          : visible,
        knownEntityTypes: known,
        loading: false,
      };
    }
    case "SET_ENTITY":
      return { ...state, selectedEntity: action.payload };
    case "SET_STATS":
      return { ...state, stats: action.payload };
    case "SET_SEARCH":
      return { ...state, searchResults: action.payload };
    case "TOGGLE_ENTITY_TYPE": {
      const next = new Set(state.visibleEntityTypes);
      if (next.has(action.payload)) next.delete(action.payload);
      else next.add(action.payload);
      return { ...state, visibleEntityTypes: next };
    }
    case "SET_ALL_TYPES":
      return { ...state, visibleEntityTypes: action.payload };
    case "TOGGLE_EDGE_LABELS":
      return { ...state, showEdgeLabels: !state.showEdgeLabels };
    case "SET_LOADING":
      return { ...state, loading: action.payload };
    case "SET_ERROR":
      return { ...state, error: action.payload, loading: false };
  }
}

export function useGraph() {
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [graph, stats] = await Promise.all([
          fetchGraph(GRAPH_FETCH_LIMIT),
          fetchStats(),
        ]);
        if (cancelled) return;
        dispatch({ type: "SET_GRAPH", payload: graph });
        dispatch({ type: "SET_STATS", payload: stats });
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: "SET_ERROR",
          payload: err instanceof Error ? err.message : "Failed to load graph",
        });
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectEntity = useCallback(async (name: string) => {
    try {
      const entity = await fetchEntity(name);
      dispatch({ type: "SET_ENTITY", payload: entity });
    } catch (err) {
      console.error("Failed to fetch entity:", err);
    }
  }, []);

  const clearEntity = useCallback(() => {
    dispatch({ type: "SET_ENTITY", payload: null });
  }, []);

  const toggleEntityType = useCallback((entityType: string) => {
    dispatch({ type: "TOGGLE_ENTITY_TYPE", payload: entityType });
  }, []);

  const selectAllTypes = useCallback(() => {
    if (!state.graph) return;
    const all = new Set(state.graph.entities.map((e) => e.entity_type));
    dispatch({ type: "SET_ALL_TYPES", payload: all });
  }, [state.graph]);

  const clearAllTypes = useCallback(() => {
    dispatch({ type: "SET_ALL_TYPES", payload: new Set() });
  }, []);

  const toggleEdgeLabels = useCallback(() => {
    dispatch({ type: "TOGGLE_EDGE_LABELS" });
  }, []);

  const entityTypes = useMemo(() => {
    if (!state.graph) return [];
    const counts = new Map<string, number>();
    for (const e of state.graph.entities) {
      counts.set(e.entity_type, (counts.get(e.entity_type) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([type, count]) => ({ type, count }));
  }, [state.graph]);

  const refreshGraph = useCallback(async () => {
    try {
      const [graph, stats] = await Promise.all([
        fetchGraph(GRAPH_FETCH_LIMIT),
        fetchStats(),
      ]);
      dispatch({ type: "SET_GRAPH", payload: graph });
      dispatch({ type: "SET_STATS", payload: stats });
    } catch (err) {
      console.error("Failed to refresh graph:", err);
    }
  }, []);

  /**
   * Set when the server holds more entities than it returned, so the picture on
   * screen is a subset. Non-null value is how many were left out.
   */
  const truncatedBy = useMemo(() => {
    const g = state.graph;
    if (!g) return null;
    const missing = g.total_entities - g.entities.length;
    return missing > 0 ? missing : null;
  }, [state.graph]);

  return {
    state,
    truncatedBy,
    selectEntity,
    clearEntity,
    toggleEntityType,
    selectAllTypes,
    clearAllTypes,
    toggleEdgeLabels,
    refreshGraph,
    entityTypes,
  };
}
