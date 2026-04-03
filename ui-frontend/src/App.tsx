import { useState, useCallback, useRef, useMemo } from "react";
import { useGraph } from "./hooks/useGraph";
import { useSearch } from "./hooks/useSearch";
import { useTheme } from "./hooks/useTheme";
import { DEFAULT_PHYSICS } from "./engine/ForceEngine";
import type { PhysicsConfig } from "./engine/ForceEngine";
import GraphCanvas from "./components/GraphCanvas";
import type { GraphCanvasHandle } from "./components/GraphCanvas";
import Sidebar from "./components/Sidebar";
import DetailPanel from "./components/DetailPanel";
import { addEntity, addObservations, addRelationship, updateEntity, deleteEntity } from "./api/client";

export default function App() {
  const {
    state,
    selectEntity,
    clearEntity,
    toggleEntityType,
    selectAllTypes,
    clearAllTypes,
    refreshGraph,
    entityTypes,
  } = useGraph();

  const { query, results, loading: searchLoading, search, clearSearch } = useSearch();
  const { theme, toggleTheme } = useTheme();
  const [physics, setPhysics] = useState<PhysicsConfig>({ ...DEFAULT_PHYSICS });
  const canvasRef = useRef<GraphCanvasHandle>(null);
  const [sidebarExpanded, setSidebarExpanded] = useState(false);

  // Collect all entity names for clickable observation keywords
  const allEntityNames = useMemo(
    () => (state.graph?.entities ?? []).map((e) => e.name),
    [state.graph],
  );

  const handlePhysicsChange = useCallback((partial: Partial<PhysicsConfig>) => {
    setPhysics((prev) => ({ ...prev, ...partial }));
  }, []);

  const handleSelectNode = useCallback(
    (name: string) => {
      void selectEntity(name);
    },
    [selectEntity],
  );

  const handleDeselectNode = useCallback(() => {
    clearEntity();
  }, [clearEntity]);

  const handleSearchSelect = useCallback(
    (name: string) => {
      void selectEntity(name);
      // Focus the canvas on the selected node
      canvasRef.current?.focusNode(name);
    },
    [selectEntity],
  );

  const handleAddEntity = useCallback(
    async (name: string, type: string, description: string, observations?: string[]) => {
      await addEntity(name, type, description);
      if (observations && observations.length > 0) {
        await addObservations(name, observations);
      }
      await refreshGraph();
    },
    [refreshGraph],
  );

  const handleAddRelationship = useCallback(
    async (source: string, target: string, relType: string) => {
      await addRelationship(source, target, relType);
      await refreshGraph();
    },
    [refreshGraph],
  );

  const handleUpdateEntity = useCallback(
    async (name: string, fields: { description?: string; entity_type?: string }) => {
      await updateEntity(name, fields);
      // Re-fetch entity details and graph
      await selectEntity(name);
      await refreshGraph();
    },
    [selectEntity, refreshGraph],
  );

  const handleDeleteEntity = useCallback(
    async (name: string) => {
      await deleteEntity(name);
      clearEntity();
      await refreshGraph();
    },
    [clearEntity, refreshGraph],
  );

  // Loading state
  if (state.loading) {
    return (
      <div className="app-shell" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", color: "var(--color-text-muted)" }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>Loading graph...</div>
        </div>
      </div>
    );
  }

  // Error state
  if (state.error) {
    return (
      <div className="app-shell" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", maxWidth: 400 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--color-danger)", marginBottom: 8 }}>
            Failed to load graph
          </div>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>{state.error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell" data-sidebar={sidebarExpanded ? "expanded" : "collapsed"}>
      {/* Title bar */}
      <div className="title-bar">
        <div className="title-bar-brand">
          <svg className="title-bar-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><circle cx="18" cy="18" r="3" /><circle cx="6" cy="18" r="3" />
            <line x1="8.5" y1="7.5" x2="15.5" y2="16.5" /><line x1="15.5" y1="7.5" x2="8.5" y2="16.5" />
          </svg>
          <span className="title-bar-name">GraphMem <span className="title-bar-mcp">MCP</span></span>
        </div>
        {state.stats && (
          <div className="title-bar-stats">
            <span>{state.stats.entity_count} entities</span>
            <span className="title-bar-dot" />
            <span>{state.stats.relationship_count} relations</span>
            <span className="title-bar-dot" />
            <span>{state.stats.observation_count} observations</span>
          </div>
        )}
      </div>

      <GraphCanvas
        ref={canvasRef}
        graph={state.graph}
        visibleEntityTypes={state.visibleEntityTypes}
        selectedNodeId={state.selectedEntity?.name ?? null}
        physics={physics}
        onSelectNode={handleSelectNode}
        onDeselectNode={handleDeselectNode}
      />
      <Sidebar
        query={query}
        searchResults={results?.results ?? []}
        searchLoading={searchLoading}
        graphEntities={state.graph?.entities ?? []}
        onSearch={search}
        onClearSearch={clearSearch}
        onSelectResult={handleSearchSelect}
        entityTypes={entityTypes}
        visibleEntityTypes={state.visibleEntityTypes}
        onToggleType={toggleEntityType}
        onSelectAllTypes={selectAllTypes}
        onClearAllTypes={clearAllTypes}
        onAddEntity={handleAddEntity}
        onAddRelationship={handleAddRelationship}
        physics={physics}
        onPhysicsChange={handlePhysicsChange}
        stats={state.stats}
        theme={theme}
        onToggleTheme={toggleTheme}
        onExpandChange={setSidebarExpanded}
        selectedEntityName={state.selectedEntity?.name ?? null}
        onDeleteEntity={handleDeleteEntity}
        onUpdateEntity={handleUpdateEntity}
      />
      <DetailPanel
        entity={state.selectedEntity}
        onClose={handleDeselectNode}
        onNavigate={handleSelectNode}
        onUpdateEntity={handleUpdateEntity}
        onDeleteEntity={handleDeleteEntity}
        allEntityNames={allEntityNames}
      />
    </div>
  );
}
