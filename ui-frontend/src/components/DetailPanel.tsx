import { useMemo, type ReactNode } from "react";
import type { EntityResponse } from "../types/graph";
import { entityColor } from "../utils/colors";

// ── SVG Icons ──

function IconX() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function IconArrowRight() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" />
    </svg>
  );
}

function IconArrowLeft() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="19" y1="12" x2="5" y2="12" /><polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

function IconInfo() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );
}

export interface DetailPanelProps {
  entity: EntityResponse | null;
  onClose: () => void;
  onNavigate: (name: string) => void;
  allEntityNames?: string[];
}

export default function DetailPanel({
  entity, onClose, onNavigate, allEntityNames,
}: DetailPanelProps) {
  const open = entity !== null;

  const outgoing = useMemo(
    () => entity?.relationships.filter((r) => r.direction === "outgoing") ?? [],
    [entity],
  );
  const incoming = useMemo(
    () => entity?.relationships.filter((r) => r.direction === "incoming") ?? [],
    [entity],
  );
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
  const properties = useMemo(() => {
    if (!entity) return [];
    return Object.entries(entity.properties).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
  }, [entity]);

  return (
    <div className={`detail-panel ${open ? "detail-panel--open" : ""}`}>
      {entity && (
        <>
          {/* ── Header ── */}
          <div className="detail-header">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="detail-entity-name">{entity.name}</div>
              <TypeBadge type={entity.entity_type} />
            </div>
            <button className="sidebar-icon-btn" onClick={onClose} title="Close" aria-label="Close">
              <IconX />
            </button>
          </div>

          {/* ── Scrollable body ── */}
          <div className="detail-body">

            {/* Description */}
            {entity.description && (
              <div className="panel-section">
                <div className="sec-title">Description</div>
                <p className="detail-description">{entity.description}</p>
              </div>
            )}

            {/* Properties */}
            {properties.length > 0 && (
              <div className="panel-section">
                <div className="sec-title">Properties ({properties.length})</div>
                <div className="detail-props-list">
                  {properties.map(([key, val]) => (
                    <div key={key} className="detail-prop-row">
                      <span className="detail-prop-key">{key}</span>
                      <span className="detail-prop-val">{String(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Relationships */}
            {(outgoing.length > 0 || incoming.length > 0) && (
              <div className="panel-section">
                <div className="sec-title">
                  Relationships ({outgoing.length + incoming.length})
                </div>
                {outgoing.length > 0 && (
                  <div style={{ marginBottom: outgoing.length > 0 && incoming.length > 0 ? 10 : 0 }}>
                    <div className="detail-rel-direction-label">Outgoing</div>
                    {outgoing.map((r, i) => (
                      <RelRow key={`o-${i}`} direction="outgoing" relType={r.relationship_type} targetName={r.target} onNavigate={onNavigate} />
                    ))}
                  </div>
                )}
                {incoming.length > 0 && (
                  <div>
                    <div className="detail-rel-direction-label">Incoming</div>
                    {incoming.map((r, i) => (
                      <RelRow key={`i-${i}`} direction="incoming" relType={r.relationship_type} targetName={r.source} onNavigate={onNavigate} />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Observations */}
            {observations.length > 0 && (
              <div className="panel-section" style={{ borderBottom: "none" }}>
                <div className="sec-title">Observations ({observations.length})</div>
                <div className="detail-obs-list">
                  {observations.map((obs, i) => (
                    <div key={obs.id || i} className="detail-obs-card">
                      <div className="detail-obs-content">
                        {allEntityNames
                          ? highlightEntities(obs.content, allEntityNames, entity.name, onNavigate)
                          : obs.content}
                      </div>
                      {obs.source && (
                        <div className="detail-obs-source">{obs.source}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Empty state — no description, no properties, no relationships, no observations */}
            {!entity.description && properties.length === 0 && outgoing.length === 0 && incoming.length === 0 && observations.length === 0 && (
              <div className="detail-empty-state">
                <IconInfo />
                <span>No details yet. Use the Edit panel on the left sidebar to add information.</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}


// ── Type badge (read-only) ──

function TypeBadge({ type }: { type: string }) {
  const color = entityColor(type);
  return (
    <span
      className="detail-type-badge"
      style={{
        background: color + "18",
        color,
        border: `1px solid ${color}33`,
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />
      {type}
    </span>
  );
}


// ── Relationship row (clickable navigation) ──

function RelRow({
  direction,
  relType,
  targetName,
  onNavigate,
}: {
  direction: "outgoing" | "incoming";
  relType: string;
  targetName: string;
  onNavigate: (name: string) => void;
}) {
  return (
    <button
      className="detail-rel-row"
      onClick={() => onNavigate(targetName)}
      title={`Navigate to ${targetName}`}
    >
      {direction === "outgoing" ? <IconArrowRight /> : <IconArrowLeft />}
      <span className="detail-rel-type">{relType}</span>
      <span className="detail-rel-target">{targetName}</span>
    </button>
  );
}


// ── Observation text with clickable entity names ──

function highlightEntities(
  text: string,
  entityNames: string[],
  currentEntity: string,
  onNavigate: (name: string) => void,
): ReactNode {
  if (entityNames.length === 0) return text;

  const names = entityNames
    .filter((n) => n.toLowerCase() !== currentEntity.toLowerCase())
    .sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;

  const escaped = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");

  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const matched = match[0]!;
    const canonical = names.find((n) => n.toLowerCase() === matched.toLowerCase()) ?? matched;
    parts.push(
      <button
        key={key++}
        onClick={() => onNavigate(canonical)}
        className="detail-entity-link"
        title={`Go to ${canonical}`}
      >
        {matched}
      </button>,
    );
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : text;
}
