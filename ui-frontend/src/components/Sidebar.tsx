import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import type { PhysicsConfig } from "../engine/ForceEngine";
import { DEFAULT_PHYSICS } from "../engine/ForceEngine";
import { entityColor } from "../utils/colors";
import type { SearchResult, StatsResponse, GraphEntity, EntityResponse } from "../types/graph";

// ── SVG Icons (inline, no emoji) ──

function IconSearch() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function IconFilter() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </svg>
  );
}

function IconPlus() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function IconGear() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function IconSun() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function IconMoon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function IconChevronLeft() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function IconManage() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  );
}

function IconSmallPlus() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconCancel() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function IconEdit() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

// ── Tiny icon button ──

function TinyBtn({ onClick, title, color, children, disabled }: {
  onClick: () => void; title: string; color?: string; children: React.ReactNode; disabled?: boolean;
}) {
  return (
    <button
      className="detail-tiny-btn"
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      title={title}
      aria-label={title}
      disabled={disabled}
      style={color ? { color } : undefined}
    >
      {children}
    </button>
  );
}

// ── Types ──

type Tab = "search" | "filter" | "add" | "settings" | "manage";

export interface SidebarProps {
  // Search
  query: string;
  searchResults: SearchResult[];
  searchLoading: boolean;
  onSearch: (q: string) => void;
  onClearSearch: () => void;
  onSelectResult: (name: string) => void;
  // Local graph entities for local matching
  graphEntities: GraphEntity[];
  // Filter
  entityTypes: { type: string; count: number }[];
  visibleEntityTypes: Set<string>;
  onToggleType: (type: string) => void;
  onSelectAllTypes: () => void;
  onClearAllTypes: () => void;
  // Add entity
  onAddEntity: (name: string, type: string, description: string, observations?: string[]) => Promise<void>;
  onAddRelationship: (source: string, target: string, relType: string) => Promise<void>;
  // Physics
  physics: PhysicsConfig;
  onPhysicsChange: (p: Partial<PhysicsConfig>) => void;
  // Stats
  stats: StatsResponse | null;
  // Theme
  theme: "light" | "dark";
  onToggleTheme: () => void;
  // Sidebar expand state callback
  onExpandChange?: (expanded: boolean) => void;
  // Manage entity — full editing
  selectedEntity: EntityResponse | null;
  onDeleteEntity: (name: string) => Promise<void>;
  onUpdateEntity: (name: string, fields: { name?: string; description?: string; entity_type?: string; properties?: Record<string, unknown> }) => Promise<void>;
  onAddObservations: (entityName: string, observations: string[]) => Promise<void>;
  onUpdateObservation: (obsId: string, entityName: string, content: string) => Promise<void>;
  onDeleteObservation: (obsId: string, entityName: string) => Promise<void>;
}

export default function Sidebar({
  query,
  searchResults,
  searchLoading,
  onSearch,
  onClearSearch,
  onSelectResult,
  graphEntities,
  entityTypes,
  visibleEntityTypes,
  onToggleType,
  onSelectAllTypes,
  onClearAllTypes,
  onAddEntity,
  onAddRelationship,
  physics,
  onPhysicsChange,
  stats,
  theme,
  onToggleTheme,
  onExpandChange,
  selectedEntity,
  onDeleteEntity,
  onUpdateEntity,
  onAddObservations,
  onUpdateObservation,
  onDeleteObservation,
}: SidebarProps) {
  const [activeTab, setActiveTab] = useState<Tab | null>(null);
  const expanded = activeTab !== null;

  // Notify parent when expanded state changes
  useEffect(() => {
    onExpandChange?.(expanded);
  }, [expanded, onExpandChange]);

  const toggleTab = useCallback((tab: Tab) => {
    setActiveTab((prev) => (prev === tab ? null : tab));
  }, []);

  const closeSidebar = useCallback(() => {
    setActiveTab(null);
  }, []);

  return (
    <div className={`sidebar ${expanded ? "sidebar--expanded" : "sidebar--collapsed"}`}>
      <div style={{ display: "flex", height: "100%" }}>
        {/* Icon strip */}
        <div className="sidebar-icons">
          <SidebarIconBtn icon={<IconSearch />} active={activeTab === "search"} onClick={() => toggleTab("search")} title="Search" />
          <SidebarIconBtn icon={<IconFilter />} active={activeTab === "filter"} onClick={() => toggleTab("filter")} title="Filter" />
          <SidebarIconBtn icon={<IconPlus />} active={activeTab === "add"} onClick={() => toggleTab("add")} title="Add Entity" />
          <SidebarIconBtn icon={<IconManage />} active={activeTab === "manage"} onClick={() => toggleTab("manage")} title="Edit Entity" />
          <SidebarIconBtn icon={<IconGear />} active={activeTab === "settings"} onClick={() => toggleTab("settings")} title="Settings" />
          <div style={{ flex: 1 }} />
          <SidebarIconBtn
            icon={theme === "dark" ? <IconSun /> : <IconMoon />}
            active={false}
            onClick={onToggleTheme}
            title={theme === "dark" ? "Light mode" : "Dark mode"}
          />
        </div>

        {/* Content area */}
        {expanded && (
          <div className="sidebar-content">
            {/* Close button */}
            <div style={{ display: "flex", justifyContent: "flex-end", paddingTop: 6, paddingBottom: 2 }}>
              <button
                className="sidebar-close-btn"
                onClick={closeSidebar}
                title="Close panel"
                aria-label="Close panel"
              >
                <IconChevronLeft />
              </button>
            </div>

            {activeTab === "search" && (
              <SearchPanel
                query={query}
                results={searchResults}
                loading={searchLoading}
                graphEntities={graphEntities}
                onSearch={onSearch}
                onClear={onClearSearch}
                onSelect={onSelectResult}
              />
            )}
            {activeTab === "filter" && (
              <FilterPanel
                entityTypes={entityTypes}
                visibleTypes={visibleEntityTypes}
                onToggle={onToggleType}
                onSelectAll={onSelectAllTypes}
                onClearAll={onClearAllTypes}
                stats={stats}
              />
            )}
            {activeTab === "add" && (
              <AddEntityPanel onAdd={onAddEntity} onAddRelationship={onAddRelationship} graphEntities={graphEntities} />
            )}
            {activeTab === "manage" && (
              <ManagePanel
                selectedEntity={selectedEntity}
                onDelete={onDeleteEntity}
                onUpdate={onUpdateEntity}
                onAddObservations={onAddObservations}
                onUpdateObservation={onUpdateObservation}
                onDeleteObservation={onDeleteObservation}
              />
            )}
            {activeTab === "settings" && (
              <SettingsPanel physics={physics} onChange={onPhysicsChange} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Icon Button ──

function SidebarIconBtn({
  icon,
  active,
  onClick,
  title,
}: {
  icon: React.ReactNode;
  active: boolean;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      className={`sidebar-icon-btn ${active ? "sidebar-icon-btn--active" : ""}`}
      onClick={onClick}
      title={title}
      aria-label={title}
    >
      {icon}
    </button>
  );
}

// ── Search Panel ──

function SearchPanel({
  query,
  results,
  loading,
  graphEntities,
  onSearch,
  onClear,
  onSelect,
}: {
  query: string;
  results: SearchResult[];
  loading: boolean;
  graphEntities: GraphEntity[];
  onSearch: (q: string) => void;
  onClear: () => void;
  onSelect: (name: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [focusIdx, setFocusIdx] = useState(-1);
  const [collapsedTypes, setCollapsedTypes] = useState<Set<string>>(new Set());

  // Auto-focus input when panel opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // When query is empty: show ALL graph entities. When query has text: filter locally + merge API results.
  const displayItems: { name: string; entity_type: string }[] = (() => {
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) {
      return [...graphEntities]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((e) => ({ name: e.name, entity_type: e.entity_type }));
    }
    const localMatches = graphEntities
      .filter(
        (e) =>
          e.name.toLowerCase().includes(trimmed) ||
          e.entity_type.toLowerCase().includes(trimmed),
      )
      .slice(0, 8);
    return mergeResults(localMatches, results);
  })();

  // Group items by entity_type
  const grouped: { type: string; items: { name: string; entity_type: string }[] }[] = (() => {
    const map = new Map<string, { name: string; entity_type: string }[]>();
    for (const item of displayItems) {
      const arr = map.get(item.entity_type);
      if (arr) {
        arr.push(item);
      } else {
        map.set(item.entity_type, [item]);
      }
    }
    return [...map.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([type, items]) => ({ type, items }));
  })();

  const toggleTypeCollapsed = useCallback((type: string) => {
    setCollapsedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }, []);

  const visibleItems = grouped.flatMap((g) =>
    collapsedTypes.has(g.type) ? [] : g.items,
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(i + 1, visibleItems.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter" && focusIdx >= 0 && focusIdx < visibleItems.length) {
        e.preventDefault();
        const item = visibleItems[focusIdx];
        if (item) {
          onSelect(item.name);
          onClear();
        }
      } else if (e.key === "Escape") {
        onClear();
      }
    },
    [visibleItems, focusIdx, onSelect, onClear],
  );

  let flatIdx = 0;

  return (
    <div className="panel-section" style={{ borderBottom: "none" }}>
      <div className="sec-title">Search</div>
      <div style={{ position: "relative" }}>
        <input
          ref={inputRef}
          className="panel-input"
          placeholder="Search entities..."
          value={query}
          onChange={(e) => {
            onSearch(e.target.value);
            setFocusIdx(-1);
          }}
          onKeyDown={handleKeyDown}
        />
        {loading && (
          <div style={{ position: "absolute", right: 10, top: 8, fontSize: 10, color: "var(--color-text-muted)" }}>
            ...
          </div>
        )}
      </div>
      {grouped.length > 0 && (
        <div style={{ marginTop: 6, maxHeight: "calc(100vh - 200px)", overflowY: "auto" }}>
          {grouped.map((group) => {
            const isCollapsed = collapsedTypes.has(group.type);
            return (
              <div key={group.type} style={{ marginBottom: 6 }}>
                <button
                  className="search-group-header"
                  onClick={() => toggleTypeCollapsed(group.type)}
                >
                  <span
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: entityColor(group.type),
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ flex: 1, textAlign: "left" }}>
                    {group.type}
                  </span>
                  <span style={{ fontSize: 10, color: "var(--color-text-muted)" }}>
                    {group.items.length}
                  </span>
                  <svg
                    width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    style={{ transition: "transform 150ms", transform: isCollapsed ? "rotate(-90deg)" : "rotate(0)" }}
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                {!isCollapsed && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 1, paddingLeft: 4 }}>
                    {group.items.map((item) => {
                      const myIdx = flatIdx++;
                      return (
                        <button
                          key={item.name}
                          className="panel-btn"
                          style={{
                            justifyContent: "flex-start",
                            background: myIdx === focusIdx ? "var(--color-accent-glow)" : undefined,
                            borderColor: myIdx === focusIdx ? "var(--color-accent)" : undefined,
                            padding: "5px 8px",
                          }}
                          onClick={() => {
                            onSelect(item.name);
                            onClear();
                          }}
                        >
                          <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {item.name}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
                {isCollapsed && (() => { flatIdx += 0; return null; })()}
              </div>
            );
          })}
        </div>
      )}
      {displayItems.length === 0 && query.trim() && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--color-text-muted)" }}>
          No matches found
        </div>
      )}
    </div>
  );
}

function mergeResults(
  local: GraphEntity[],
  api: SearchResult[],
): { name: string; entity_type: string }[] {
  const seen = new Set<string>();
  const out: { name: string; entity_type: string }[] = [];
  for (const l of local) {
    if (!seen.has(l.name)) {
      seen.add(l.name);
      out.push({ name: l.name, entity_type: l.entity_type });
    }
  }
  for (const a of api) {
    if (!seen.has(a.name)) {
      seen.add(a.name);
      out.push({ name: a.name, entity_type: a.entity_type });
    }
  }
  return out.slice(0, 20);
}

// ── Filter Panel ──

function FilterPanel({
  entityTypes,
  visibleTypes,
  onToggle,
  onSelectAll,
  onClearAll,
  stats,
}: {
  entityTypes: { type: string; count: number }[];
  visibleTypes: Set<string>;
  onToggle: (type: string) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  stats: StatsResponse | null;
}) {
  return (
    <>
      <div className="panel-section">
        <div className="sec-title">Entity Types</div>
        <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
          <button className="panel-btn" style={{ flex: 1 }} onClick={onSelectAll}>All</button>
          <button className="panel-btn" style={{ flex: 1 }} onClick={onClearAll}>None</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {entityTypes.map(({ type, count }) => (
            <label
              key={type}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "4px 6px",
                borderRadius: 5,
                cursor: "pointer",
                background: visibleTypes.has(type) ? "var(--color-surface-2)" : "transparent",
                transition: "background 120ms",
              }}
            >
              <input
                type="checkbox"
                checked={visibleTypes.has(type)}
                onChange={() => onToggle(type)}
                style={{ accentColor: entityColor(type) }}
              />
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: entityColor(type),
                  flexShrink: 0,
                }}
              />
              <span style={{ flex: 1, fontSize: 12 }}>{type}</span>
              <span style={{ fontSize: 10, color: "var(--color-text-muted)" }}>{count}</span>
            </label>
          ))}
        </div>
      </div>
      {stats && (
        <div className="panel-section">
          <div className="sec-title">Stats</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            <div className="stat-card">
              <div className="stat-val">{stats.entity_count}</div>
              <div className="stat-lbl">Entities</div>
            </div>
            <div className="stat-card">
              <div className="stat-val">{stats.relationship_count}</div>
              <div className="stat-lbl">Relations</div>
            </div>
            <div className="stat-card">
              <div className="stat-val">{stats.observation_count}</div>
              <div className="stat-lbl">Observations</div>
            </div>
            <div className="stat-card">
              <div className="stat-val">{Object.keys(stats.entity_type_distribution).length}</div>
              <div className="stat-lbl">Types</div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Add Entity Panel ──

function AddEntityPanel({
  onAdd,
  onAddRelationship,
  graphEntities,
}: {
  onAdd: (name: string, type: string, desc: string, observations?: string[]) => Promise<void>;
  onAddRelationship: (source: string, target: string, relType: string) => Promise<void>;
  graphEntities: GraphEntity[];
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("");
  const [desc, setDesc] = useState("");
  const [obs, setObs] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const [relSource, setRelSource] = useState("");
  const [relTarget, setRelTarget] = useState("");
  const [relType, setRelType] = useState("");
  const [relBusy, setRelBusy] = useState(false);
  const [relMsg, setRelMsg] = useState("");

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!name.trim() || !type.trim()) return;
      setBusy(true);
      setMsg("");
      try {
        const observations = obs.trim()
          ? obs.split("\n").map((l) => l.trim()).filter(Boolean)
          : undefined;
        await onAdd(name.trim(), type.trim(), desc.trim(), observations);
        setMsg("Added!");
        setName("");
        setType("");
        setDesc("");
        setObs("");
        setTimeout(() => setMsg(""), 2000);
      } catch (err) {
        setMsg(err instanceof Error ? err.message : "Failed");
      } finally {
        setBusy(false);
      }
    },
    [name, type, desc, obs, onAdd],
  );

  const handleRelSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!relSource.trim() || !relTarget.trim() || !relType.trim()) return;
      setRelBusy(true);
      setRelMsg("");
      try {
        await onAddRelationship(relSource.trim(), relTarget.trim(), relType.trim());
        setRelMsg("Linked!");
        setRelSource("");
        setRelTarget("");
        setRelType("");
        setTimeout(() => setRelMsg(""), 2000);
      } catch (err) {
        setRelMsg(err instanceof Error ? err.message : "Failed");
      } finally {
        setRelBusy(false);
      }
    },
    [relSource, relTarget, relType, onAddRelationship],
  );

  const entityNames = graphEntities.map((e) => e.name).sort();

  return (
    <>
      <div className="panel-section">
        <div className="sec-title">New Entity</div>
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input className="panel-input" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required />
          <input className="panel-input" placeholder="Type (e.g. person)" value={type} onChange={(e) => setType(e.target.value)} required />
          <textarea
            className="panel-input"
            placeholder="Description (optional)"
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            rows={2}
            style={{ resize: "vertical" }}
          />
          <textarea
            className="panel-input"
            placeholder={"Observations (one per line)\ne.g. Born in 1990\nSpeaks three languages"}
            value={obs}
            onChange={(e) => setObs(e.target.value)}
            rows={3}
            style={{ resize: "vertical" }}
          />
          <button className="panel-btn panel-btn-primary" type="submit" disabled={busy}>
            {busy ? "Adding..." : "Add Entity"}
          </button>
          {msg && <div style={{ fontSize: 11, color: msg === "Added!" ? "var(--color-success)" : "var(--color-danger)" }}>{msg}</div>}
        </form>
      </div>

      <div className="panel-section" style={{ borderBottom: "none" }}>
        <div className="sec-title">New Relationship</div>
        <form onSubmit={handleRelSubmit} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <select className="panel-input" value={relSource} onChange={(e) => setRelSource(e.target.value)} required>
            <option value="">Source entity...</option>
            {entityNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <select className="panel-input" value={relTarget} onChange={(e) => setRelTarget(e.target.value)} required>
            <option value="">Target entity...</option>
            {entityNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <input className="panel-input" placeholder="Relationship type (e.g. knows)" value={relType} onChange={(e) => setRelType(e.target.value)} required />
          <button className="panel-btn panel-btn-primary" type="submit" disabled={relBusy}>
            {relBusy ? "Linking..." : "Add Relationship"}
          </button>
          {relMsg && <div style={{ fontSize: 11, color: relMsg === "Linked!" ? "var(--color-success)" : "var(--color-danger)" }}>{relMsg}</div>}
        </form>
      </div>
    </>
  );
}

// ── Settings (Physics) Panel ──

function SettingsPanel({
  physics,
  onChange,
}: {
  physics: PhysicsConfig;
  onChange: (p: Partial<PhysicsConfig>) => void;
}) {
  return (
    <div className="panel-section" style={{ borderBottom: "none" }}>
      <div className="sec-title">Physics Controls</div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingLeft: 2, paddingRight: 2 }}>
        <PhysicsSlider label="Repulsion" value={physics.repulsion} min={500} max={20000} step={100} onChange={(v) => onChange({ repulsion: v })} />
        <PhysicsSlider label="Spring Length" value={physics.springLen} min={30} max={400} step={5} onChange={(v) => onChange({ springLen: v })} />
        <PhysicsSlider label="Spring Strength" value={physics.springStr} min={0.01} max={0.3} step={0.005} onChange={(v) => onChange({ springStr: v })} />
        <PhysicsSlider label="Gravity" value={physics.gravity} min={0} max={0.1} step={0.002} onChange={(v) => onChange({ gravity: v })} />
        <PhysicsSlider label="Damping" value={physics.damping} min={0.5} max={0.99} step={0.01} onChange={(v) => onChange({ damping: v })} />
        <button
          className="panel-btn"
          onClick={() => onChange({ ...DEFAULT_PHYSICS })}
          style={{ marginTop: 4 }}
        >
          Reset Defaults
        </button>
      </div>

      <div style={{ marginTop: 12, fontSize: 10, color: "var(--color-text-muted)", lineHeight: 1.5 }}>
        <strong>Shortcuts</strong><br />
        Space — Reheat simulation<br />
        F — Fit to view<br />
        Esc — Deselect node<br />
        Double-click — Pin/unpin node
      </div>
    </div>
  );
}

function PhysicsSlider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
        <span>{label}</span>
        <span style={{ color: "var(--color-text-muted)", fontVariantNumeric: "tabular-nums" }}>{value}</span>
      </div>
      <input
        type="range"
        className="slider-input"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// Manage Panel — full entity editing (name, type, desc, props, obs, delete)
// ══════════════════════════════════════════════════════════════════════

function ManagePanel({
  selectedEntity,
  onDelete,
  onUpdate,
  onAddObservations,
  onUpdateObservation,
  onDeleteObservation,
}: {
  selectedEntity: EntityResponse | null;
  onDelete: (name: string) => Promise<void>;
  onUpdate: (name: string, fields: { name?: string; description?: string; entity_type?: string; properties?: Record<string, unknown> }) => Promise<void>;
  onAddObservations: (entityName: string, observations: string[]) => Promise<void>;
  onUpdateObservation: (obsId: string, entityName: string, content: string) => Promise<void>;
  onDeleteObservation: (obsId: string, entityName: string) => Promise<void>;
}) {
  const entity = selectedEntity;
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  // When selected entity changes, prefill edit fields & reset state
  useEffect(() => {
    if (entity) {
      setEditName(entity.name ?? "");
      setEditType(entity.entity_type ?? "");
      setEditDesc(entity.description ?? "");
    }
    setConfirmDelete(false);
    setFeedback(null);
  }, [entity?.name, entity?.entity_type, entity?.description]);

  const properties = useMemo(() => {
    if (!entity) return [];
    return Object.entries(entity.properties).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
  }, [entity]);

  const observations = useMemo(
    () =>
      entity?.observations
        .slice()
        .sort((a, b) => {
          const ta = a.created_at ? new Date(String(a.created_at)).getTime() : 0;
          const tb = b.created_at ? new Date(String(b.created_at)).getTime() : 0;
          return tb - ta;
        }) ?? [],
    [entity],
  );

  const handleDelete = async () => {
    if (!entity) return;
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setBusy(true);
    try {
      await onDelete(entity.name);
      setFeedback({ ok: true, msg: `Deleted "${entity.name}"` });
      setConfirmDelete(false);
    } catch {
      setFeedback({ ok: false, msg: "Delete failed" });
    } finally {
      setBusy(false);
    }
  };

  const handleSave = async () => {
    if (!entity) return;
    setBusy(true);
    try {
      const fields: { name?: string; description?: string; entity_type?: string } = {};
      if (editName.trim() && editName.trim() !== entity.name) fields.name = editName.trim();
      if (editDesc !== (entity.description ?? "")) fields.description = editDesc;
      if (editType !== (entity.entity_type ?? "")) fields.entity_type = editType;
      if (Object.keys(fields).length === 0) {
        setFeedback({ ok: true, msg: "No changes" });
        setTimeout(() => setFeedback(null), 1500);
        setBusy(false);
        return;
      }
      await onUpdate(entity.name, fields);
      setFeedback({ ok: true, msg: "Saved!" });
      setTimeout(() => setFeedback(null), 2000);
    } catch {
      setFeedback({ ok: false, msg: "Update failed" });
    } finally {
      setBusy(false);
    }
  };

  // No entity selected — show helpful prompt
  if (!entity) {
    return (
      <div className="panel-section" style={{ borderBottom: "none" }}>
        <div className="sec-title">Edit Entity</div>
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          padding: "24px 12px",
          color: "var(--color-text-muted)",
          textAlign: "center",
        }}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.4 }}>
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <div style={{ fontSize: 12, lineHeight: 1.5 }}>
            Click on a node in the graph to select it, then edit its details here.
          </div>
        </div>
      </div>
    );
  }

  // Entity selected — full editing UI
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {/* ── Entity Identity ── */}
      <div className="panel-section">
        <div className="sec-title">Entity</div>

        {/* Entity badge */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
          borderRadius: 6,
          background: "var(--color-surface-2)",
          border: "1px solid var(--color-border)",
          marginBottom: 12,
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%",
            background: entityColor(entity.entity_type),
            flexShrink: 0,
          }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--color-text)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{entity.name}</span>
          <span style={{ fontSize: 10, color: "var(--color-text-muted)" }}>{entity.entity_type}</span>
        </div>

        {/* Edit fields */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div>
            <label style={{ fontSize: 10, fontWeight: 600, color: "var(--color-text-muted)", display: "block", marginBottom: 3 }}>Name</label>
            <input
              className="panel-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              placeholder="Entity name"
            />
          </div>
          <div>
            <label style={{ fontSize: 10, fontWeight: 600, color: "var(--color-text-muted)", display: "block", marginBottom: 3 }}>Type</label>
            <input
              className="panel-input"
              value={editType}
              onChange={(e) => setEditType(e.target.value)}
              placeholder="e.g. person, concept, project"
            />
          </div>
          <div>
            <label style={{ fontSize: 10, fontWeight: 600, color: "var(--color-text-muted)", display: "block", marginBottom: 3 }}>Description</label>
            <textarea
              className="panel-input"
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
              placeholder="Description"
              rows={3}
              style={{ resize: "vertical" }}
            />
          </div>

          <button className="panel-btn panel-btn-primary" disabled={busy} onClick={() => void handleSave()}>
            {busy ? "Saving..." : "Save Changes"}
          </button>
        </div>

        {feedback && (
          <div style={{ fontSize: 11, marginTop: 8, color: feedback.ok ? "var(--color-success)" : "var(--color-danger)", textAlign: "center" }}>
            {feedback.msg}
          </div>
        )}
      </div>

      {/* ── Properties ── */}
      <div className="panel-section">
        <div className="sec-title">Properties ({properties.length})</div>
        <ManagePropertiesEditor
          properties={properties}
          onSave={async (newProps) => {
            await onUpdate(entity.name, { properties: newProps });
          }}
        />
      </div>

      {/* ── Observations ── */}
      <div className="panel-section">
        <div className="sec-title">Observations ({observations.length})</div>
        <ManageObservationsEditor
          observations={observations}
          onAdd={async (texts) => {
            await onAddObservations(entity.name, texts);
          }}
          onUpdate={async (obsId, content) => {
            await onUpdateObservation(obsId, entity.name, content);
          }}
          onDelete={async (obsId) => {
            await onDeleteObservation(obsId, entity.name);
          }}
        />
      </div>

      {/* ── Danger Zone ── */}
      <div className="panel-section" style={{ borderBottom: "none" }}>
        <div className="sec-title" style={{ borderLeftColor: "var(--color-danger)" }}>Danger Zone</div>
        <button
          className={`panel-btn ${confirmDelete ? "panel-btn-danger" : ""}`}
          disabled={busy}
          onClick={() => void handleDelete()}
          style={{ gap: 5 }}
        >
          <IconTrash />
          {confirmDelete ? "Click again to confirm delete" : "Delete Entity"}
        </button>
        {confirmDelete && (
          <button
            className="panel-btn"
            onClick={() => setConfirmDelete(false)}
            style={{ fontSize: 10, marginTop: 4 }}
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════════
// Properties editor for ManagePanel
// ══════════════════════════════════════════════════════════════════════

function ManagePropertiesEditor({ properties, onSave }: {
  properties: [string, unknown][];
  onSave: (props: Record<string, unknown>) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editVal, setEditVal] = useState("");
  const [saving, setSaving] = useState(false);
  const keyRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (adding && keyRef.current) keyRef.current.focus(); }, [adding]);

  const handleAddProp = useCallback(async () => {
    if (!newKey.trim()) return;
    setSaving(true);
    const updated: Record<string, unknown> = {};
    for (const [k, v] of properties) updated[k] = v;
    updated[newKey.trim()] = newVal;
    try { await onSave(updated); setNewKey(""); setNewVal(""); setAdding(false); } finally { setSaving(false); }
  }, [newKey, newVal, properties, onSave]);

  const handleUpdateProp = useCallback(async (key: string) => {
    setSaving(true);
    const updated: Record<string, unknown> = {};
    for (const [k, v] of properties) updated[k] = k === key ? editVal : v;
    try { await onSave(updated); setEditingKey(null); } finally { setSaving(false); }
  }, [editVal, properties, onSave]);

  const handleDeleteProp = useCallback(async (key: string) => {
    setSaving(true);
    const updated: Record<string, unknown> = {};
    for (const [k, v] of properties) { if (k !== key) updated[k] = v; }
    try { await onSave(updated); } finally { setSaving(false); }
  }, [properties, onSave]);

  return (
    <div className="detail-props-list">
      {properties.map(([key, val]) => (
        <div key={key} className="detail-prop-row">
          <span className="detail-prop-key">{key}</span>
          {editingKey === key ? (
            <div className="detail-inline-edit" style={{ flex: 1 }}>
              <input
                className="detail-inline-input"
                value={editVal}
                onChange={(e) => setEditVal(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void handleUpdateProp(key); if (e.key === "Escape") setEditingKey(null); }}
                disabled={saving}
                autoFocus
                style={{ fontSize: 11 }}
              />
              <TinyBtn onClick={() => void handleUpdateProp(key)} title="Save" color="var(--color-success)"><IconCheck /></TinyBtn>
              <TinyBtn onClick={() => setEditingKey(null)} title="Cancel"><IconCancel /></TinyBtn>
            </div>
          ) : (
            <>
              <span className="detail-prop-val">{String(val)}</span>
              <div className="detail-prop-actions">
                <TinyBtn onClick={() => { setEditingKey(key); setEditVal(String(val)); }} title="Edit"><IconEdit /></TinyBtn>
                <TinyBtn onClick={() => void handleDeleteProp(key)} title="Delete" color="var(--color-danger)"><IconTrash /></TinyBtn>
              </div>
            </>
          )}
        </div>
      ))}

      {properties.length === 0 && !adding && (
        <div style={{ fontSize: 11, color: "var(--color-text-muted)", fontStyle: "italic", padding: "4px 0" }}>
          No properties
        </div>
      )}

      {adding ? (
        <div className="detail-add-prop-form">
          <input ref={keyRef} className="detail-inline-input" placeholder="Key" value={newKey} onChange={(e) => setNewKey(e.target.value)} disabled={saving} style={{ flex: 1 }} />
          <input className="detail-inline-input" placeholder="Value" value={newVal} onChange={(e) => setNewVal(e.target.value)} disabled={saving} style={{ flex: 1 }}
            onKeyDown={(e) => { if (e.key === "Enter") void handleAddProp(); if (e.key === "Escape") setAdding(false); }}
          />
          <TinyBtn onClick={() => void handleAddProp()} title="Add" color="var(--color-success)" disabled={saving}><IconCheck /></TinyBtn>
          <TinyBtn onClick={() => setAdding(false)} title="Cancel"><IconCancel /></TinyBtn>
        </div>
      ) : (
        <button className="detail-add-btn" onClick={() => setAdding(true)}>
          <IconSmallPlus /> Add property
        </button>
      )}
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════════
// Observations editor for ManagePanel
// ══════════════════════════════════════════════════════════════════════

function ManageObservationsEditor({ observations, onAdd, onUpdate, onDelete }: {
  observations: { id: string; content: string; source?: string; created_at?: string | number | null }[];
  onAdd: (texts: string[]) => Promise<void>;
  onUpdate: (obsId: string, content: string) => Promise<void>;
  onDelete: (obsId: string) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [newObs, setNewObs] = useState("");
  const [saving, setSaving] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (adding && textRef.current) textRef.current.focus(); }, [adding]);

  const handleAdd = useCallback(async () => {
    const lines = newObs.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length === 0) return;
    setSaving(true);
    try { await onAdd(lines); setNewObs(""); setAdding(false); } finally { setSaving(false); }
  }, [newObs, onAdd]);

  return (
    <div className="detail-obs-list">
      {observations.map((obs, i) => (
        <ManageObservationCard
          key={obs.id || i}
          obs={obs}
          onUpdate={onUpdate}
          onDelete={onDelete}
        />
      ))}

      {observations.length === 0 && !adding && (
        <div style={{ fontSize: 11, color: "var(--color-text-muted)", fontStyle: "italic", padding: "4px 0" }}>
          No observations
        </div>
      )}

      {adding ? (
        <div className="detail-add-obs-form">
          <textarea
            ref={textRef}
            className="detail-inline-textarea"
            value={newObs}
            onChange={(e) => setNewObs(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") setAdding(false); }}
            disabled={saving}
            rows={3}
            placeholder={"Add observations (one per line)\ne.g. Born in 1990\nSpeaks three languages"}
          />
          <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
            <button className="panel-btn panel-btn-primary" onClick={() => void handleAdd()} disabled={saving || !newObs.trim()} style={{ flex: 1, justifyContent: "center", gap: 4, fontSize: 10 }}>
              <IconSmallPlus /> {saving ? "Adding..." : "Add"}
            </button>
            <button className="panel-btn" onClick={() => { setNewObs(""); setAdding(false); }} style={{ flex: 1, justifyContent: "center", fontSize: 10 }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button className="detail-add-btn" onClick={() => setAdding(true)}>
          <IconSmallPlus /> Add observation
        </button>
      )}
    </div>
  );
}

function ManageObservationCard({ obs, onUpdate, onDelete }: {
  obs: { id: string; content: string; source?: string };
  onUpdate: (obsId: string, content: string) => Promise<void>;
  onDelete: (obsId: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(obs.content);
  const [saving, setSaving] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setDraft(obs.content); }, [obs.content]);
  useEffect(() => { if (editing && textRef.current) { textRef.current.focus(); textRef.current.setSelectionRange(textRef.current.value.length, textRef.current.value.length); } }, [editing]);

  const save = useCallback(async () => {
    if (!draft.trim() || draft === obs.content || !obs.id) { setEditing(false); return; }
    setSaving(true);
    try { await onUpdate(obs.id, draft.trim()); } finally { setSaving(false); setEditing(false); }
  }, [draft, obs, onUpdate]);

  const handleDelete = useCallback(async () => {
    if (!obs.id) return;
    if (!confirmDel) { setConfirmDel(true); return; }
    setSaving(true);
    try { await onDelete(obs.id); } finally { setSaving(false); setConfirmDel(false); }
  }, [obs.id, confirmDel, onDelete]);

  if (editing) {
    return (
      <div className="detail-obs-card detail-obs-card--editing">
        <textarea
          ref={textRef}
          className="detail-inline-textarea"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") { setDraft(obs.content); setEditing(false); } }}
          disabled={saving}
          rows={3}
        />
        <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
          <button className="panel-btn" onClick={() => void save()} disabled={saving} style={{ flex: 1, justifyContent: "center", gap: 4, fontSize: 10 }}>
            <IconCheck /> {saving ? "Saving..." : "Save"}
          </button>
          <button className="panel-btn" onClick={() => { setDraft(obs.content); setEditing(false); }} style={{ flex: 1, justifyContent: "center", fontSize: 10 }}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="detail-obs-card">
      <div className="detail-obs-content" style={{ paddingRight: 40 }}>
        {obs.content}
      </div>
      {obs.source && (
        <div className="detail-obs-source">{obs.source}</div>
      )}
      {obs.id && (
        <div className="detail-obs-actions">
          <TinyBtn onClick={() => setEditing(true)} title="Edit observation"><IconEdit /></TinyBtn>
          {confirmDel ? (
            <>
              <TinyBtn onClick={() => void handleDelete()} title="Confirm delete" color="var(--color-danger)"><IconCheck /></TinyBtn>
              <TinyBtn onClick={() => setConfirmDel(false)} title="Cancel"><IconCancel /></TinyBtn>
            </>
          ) : (
            <TinyBtn onClick={() => void handleDelete()} title="Delete observation" color="var(--color-danger)"><IconTrash /></TinyBtn>
          )}
        </div>
      )}
    </div>
  );
}
