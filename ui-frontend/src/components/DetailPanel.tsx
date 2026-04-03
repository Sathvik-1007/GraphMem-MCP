import { useMemo, useState, useCallback, type ReactNode } from "react";
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

function IconTrash() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function IconEdit() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export interface DetailPanelProps {
  entity: EntityResponse | null;
  onClose: () => void;
  onNavigate: (name: string) => void;
  onUpdateEntity?: (name: string, fields: { description?: string; entity_type?: string }) => Promise<void>;
  onDeleteEntity?: (name: string) => Promise<void>;
  allEntityNames?: string[];
}

export default function DetailPanel({ entity, onClose, onNavigate, onUpdateEntity, onDeleteEntity, allEntityNames }: DetailPanelProps) {
  const open = entity !== null;
  const [editing, setEditing] = useState(false);
  const [editDesc, setEditDesc] = useState("");
  const [editType, setEditType] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const startEdit = useCallback(() => {
    if (!entity) return;
    setEditDesc(entity.description || "");
    setEditType(entity.entity_type || "");
    setEditing(true);
    setConfirmDelete(false);
  }, [entity]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setConfirmDelete(false);
  }, []);

  const saveEdit = useCallback(async () => {
    if (!entity || !onUpdateEntity) return;
    setSaving(true);
    try {
      const fields: { description?: string; entity_type?: string } = {};
      if (editDesc !== entity.description) fields.description = editDesc;
      if (editType !== entity.entity_type) fields.entity_type = editType;
      if (Object.keys(fields).length > 0) {
        await onUpdateEntity(entity.name, fields);
      }
      setEditing(false);
    } catch (e) {
      console.error("Failed to update entity:", e);
    } finally {
      setSaving(false);
    }
  }, [entity, editDesc, editType, onUpdateEntity]);

  const handleDelete = useCallback(async () => {
    if (!entity || !onDeleteEntity) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setSaving(true);
    try {
      await onDeleteEntity(entity.name);
      setConfirmDelete(false);
      setEditing(false);
    } catch (e) {
      console.error("Failed to delete entity:", e);
    } finally {
      setSaving(false);
    }
  }, [entity, confirmDelete, onDeleteEntity]);

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
          const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
          const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
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
          {/* Header */}
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              padding: "16px 14px 12px",
              borderBottom: "1px solid var(--color-border)",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.3, wordBreak: "break-word" }}>
                {entity.name}
              </div>
              <div style={{ marginTop: 4 }}>
                <TypeBadge type={entity.entity_type} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
              {!editing && onUpdateEntity && (
                <button
                  className="sidebar-icon-btn"
                  onClick={startEdit}
                  title="Edit entity"
                  aria-label="Edit entity"
                >
                  <IconEdit />
                </button>
              )}
              <button
                className="sidebar-icon-btn"
                onClick={onClose}
                title="Close"
                aria-label="Close detail panel"
              >
                <IconX />
              </button>
            </div>
          </div>

          {/* Edit form */}
          {editing && (
            <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--color-border)", background: "var(--color-surface-2)" }}>
              <div style={{ marginBottom: 8 }}>
                <label style={{ fontSize: 10, fontWeight: 600, color: "var(--color-text-muted)", display: "block", marginBottom: 3 }}>Type</label>
                <input
                  className="sidebar-input"
                  value={editType}
                  onChange={(e) => setEditType(e.target.value)}
                  style={{ width: "100%" }}
                />
              </div>
              <div style={{ marginBottom: 8 }}>
                <label style={{ fontSize: 10, fontWeight: 600, color: "var(--color-text-muted)", display: "block", marginBottom: 3 }}>Description</label>
                <textarea
                  className="sidebar-input"
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  rows={3}
                  style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                />
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="panel-btn" onClick={() => void saveEdit()} disabled={saving} style={{ flex: 1, justifyContent: "center", gap: 4, color: "var(--color-accent)" }}>
                  <IconCheck /> {saving ? "Saving..." : "Save"}
                </button>
                <button className="panel-btn" onClick={cancelEdit} style={{ flex: 1, justifyContent: "center" }}>
                  Cancel
                </button>
              </div>
              {onDeleteEntity && (
                <button
                  className="panel-btn"
                  onClick={() => void handleDelete()}
                  disabled={saving}
                  style={{ width: "100%", justifyContent: "center", gap: 4, marginTop: 6, color: "var(--color-danger, #e74c3c)" }}
                >
                  <IconTrash /> {confirmDelete ? "Click again to confirm delete" : "Delete entity"}
                </button>
              )}
            </div>
          )}

          {/* Body */}
          <div style={{ flex: 1, overflowY: "auto", padding: "0 14px 14px" }}>
            {/* Description */}
            {entity.description && (
              <div className="panel-section">
                <div className="sec-title">Description</div>
                <p style={{ fontSize: 12, lineHeight: 1.6, color: "var(--color-text-secondary)" }}>
                  {entity.description}
                </p>
              </div>
            )}

            {/* Properties */}
            {properties.length > 0 && (
              <div className="panel-section">
                <div className="sec-title">Properties</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {properties.map(([key, val]) => (
                    <div
                      key={key}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: 11,
                        padding: "3px 0",
                      }}
                    >
                      <span style={{ color: "var(--color-text-muted)" }}>{key}</span>
                      <span style={{ textAlign: "right", maxWidth: "60%", wordBreak: "break-word" }}>
                        {String(val)}
                      </span>
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
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginBottom: 4 }}>
                      Outgoing
                    </div>
                    {outgoing.map((r, i) => (
                      <RelRow
                        key={`o-${i}`}
                        direction="outgoing"
                        relType={r.relationship_type}
                        targetName={r.target}
                        onNavigate={onNavigate}
                      />
                    ))}
                  </div>
                )}

                {incoming.length > 0 && (
                  <div>
                    <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginBottom: 4 }}>
                      Incoming
                    </div>
                    {incoming.map((r, i) => (
                      <RelRow
                        key={`i-${i}`}
                        direction="incoming"
                        relType={r.relationship_type}
                        targetName={r.source}
                        onNavigate={onNavigate}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Observations */}
            {observations.length > 0 && (
              <div className="panel-section">
                <div className="sec-title">Observations ({observations.length})</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {observations.map((obs, i) => (
                    <div
                      key={i}
                      style={{
                        fontSize: 11.5,
                        lineHeight: 1.55,
                        color: "var(--color-text-secondary)",
                        padding: "6px 8px",
                        background: "var(--color-surface-2)",
                        borderRadius: 6,
                        borderLeft: "2px solid var(--color-accent)",
                      }}
                    >
                      {allEntityNames && entity
                        ? highlightEntities(obs.content, allEntityNames, entity.name, onNavigate)
                        : obs.content}
                      {obs.source && (
                        <div style={{ fontSize: 9, color: "var(--color-text-muted)", marginTop: 3 }}>
                          {obs.source}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
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

  // Filter out the current entity name and sort by length descending so longer names match first
  const names = entityNames
    .filter((n) => n.toLowerCase() !== currentEntity.toLowerCase())
    .sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;

  // Build a regex that matches any entity name (case-insensitive)
  // No word boundaries — multi-word names like "The Forgotten Story" need substring matching
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
    // Find the canonical name (preserve original casing from entity list)
    const canonical = names.find((n) => n.toLowerCase() === matched.toLowerCase()) ?? matched;
    parts.push(
      <button
        key={key++}
        onClick={() => onNavigate(canonical)}
        style={{
          display: "inline",
          padding: "1px 5px",
          margin: "0 1px",
          fontSize: "inherit",
          lineHeight: "inherit",
          fontWeight: 600,
          color: "var(--color-accent)",
          background: "var(--color-accent-bg, rgba(99,102,241,0.1))",
          border: "1px solid var(--color-accent-border, rgba(99,102,241,0.2))",
          borderRadius: 4,
          cursor: "pointer",
          fontFamily: "inherit",
          verticalAlign: "baseline",
        }}
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

// ── Sub-components ──

function TypeBadge({ type }: { type: string }) {
  const color = entityColor(type);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "2px 8px",
        fontSize: 10,
        fontWeight: 600,
        borderRadius: 100,
        background: color + "18",
        color,
        border: `1px solid ${color}33`,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
        }}
      />
      {type}
    </span>
  );
}

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
      className="panel-btn"
      style={{
        justifyContent: "flex-start",
        gap: 6,
        marginBottom: 3,
        fontSize: 11,
      }}
      onClick={() => onNavigate(targetName)}
    >
      {direction === "outgoing" ? <IconArrowRight /> : <IconArrowLeft />}
      <span
        style={{
          color: "var(--color-accent)",
          fontSize: 9.5,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          flexShrink: 0,
        }}
      >
        {relType}
      </span>
      <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {targetName}
      </span>
    </button>
  );
}
