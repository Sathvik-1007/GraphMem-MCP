import { useMemo, useState, useCallback, useEffect, useRef, type ReactNode } from "react";
import type { EntityResponse, EntityObservation } from "../types/graph";
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
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
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

function IconCheck() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconPlus() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
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


// ── Tiny icon button used throughout ──

function TinyBtn({ onClick, title, color, children, disabled }: {
  onClick: () => void; title: string; color?: string; children: ReactNode; disabled?: boolean;
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

export interface DetailPanelProps {
  entity: EntityResponse | null;
  onClose: () => void;
  onNavigate: (name: string) => void;
  onUpdateEntity?: (name: string, fields: { name?: string; description?: string; entity_type?: string; properties?: Record<string, unknown> }) => Promise<void>;
  onDeleteEntity?: (name: string) => Promise<void>;
  onAddObservations?: (entityName: string, observations: string[]) => Promise<void>;
  onUpdateObservation?: (obsId: string, entityName: string, content: string) => Promise<void>;
  onDeleteObservation?: (obsId: string, entityName: string) => Promise<void>;
  allEntityNames?: string[];
}

export default function DetailPanel({
  entity, onClose, onNavigate, onUpdateEntity, onDeleteEntity,
  onAddObservations, onUpdateObservation, onDeleteObservation,
  allEntityNames,
}: DetailPanelProps) {
  const open = entity !== null;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState("");

  // Reset confirm state when entity changes
  useEffect(() => {
    setConfirmDelete(false);
    setFeedback("");
  }, [entity?.name]);

  const handleDelete = useCallback(async () => {
    if (!entity || !onDeleteEntity) return;
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setDeleting(true);
    try {
      await onDeleteEntity(entity.name);
    } catch {
      setFeedback("Delete failed");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
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
              <EditableField
                value={entity.name}
                placeholder="Entity name"
                className="detail-entity-name"
                onSave={async (v) => {
                  if (v !== entity.name && onUpdateEntity) {
                    await onUpdateEntity(entity.name, { name: v });
                  }
                }}
              />
              <EditableTypeBadge
                type={entity.entity_type}
                onSave={async (v) => {
                  if (v !== entity.entity_type && onUpdateEntity) {
                    await onUpdateEntity(entity.name, { entity_type: v });
                  }
                }}
              />
            </div>
            <button className="sidebar-icon-btn" onClick={onClose} title="Close" aria-label="Close">
              <IconX />
            </button>
          </div>

          {/* ── Scrollable body ── */}
          <div className="detail-body">
            {/* Description — always show, editable */}
            <div className="panel-section">
              <div className="sec-title">Description</div>
              <EditableTextArea
                value={entity.description || ""}
                placeholder="No description — click to add one..."
                onSave={async (v) => {
                  if (onUpdateEntity) {
                    await onUpdateEntity(entity.name, { description: v });
                  }
                }}
              />
            </div>

            {/* Properties — editable rows + add */}
            <div className="panel-section">
              <div className="sec-title">Properties ({properties.length})</div>
              <PropertiesEditor
                properties={properties}
                onSave={async (newProps) => {
                  if (onUpdateEntity) {
                    await onUpdateEntity(entity.name, { properties: newProps });
                  }
                }}
              />
            </div>

            {/* Relationships */}
            {(outgoing.length > 0 || incoming.length > 0) && (
              <div className="panel-section">
                <div className="sec-title">
                  Relationships ({outgoing.length + incoming.length})
                </div>
                {outgoing.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginBottom: 4 }}>Outgoing</div>
                    {outgoing.map((r, i) => (
                      <RelRow key={`o-${i}`} direction="outgoing" relType={r.relationship_type} targetName={r.target} onNavigate={onNavigate} />
                    ))}
                  </div>
                )}
                {incoming.length > 0 && (
                  <div>
                    <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginBottom: 4 }}>Incoming</div>
                    {incoming.map((r, i) => (
                      <RelRow key={`i-${i}`} direction="incoming" relType={r.relationship_type} targetName={r.source} onNavigate={onNavigate} />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Observations — editable cards + add */}
            <div className="panel-section">
              <div className="sec-title">Observations ({observations.length})</div>
              <ObservationsEditor
                observations={observations}
                entityName={entity.name}
                allEntityNames={allEntityNames}
                onNavigate={onNavigate}
                onAdd={onAddObservations ? async (texts) => {
                  await onAddObservations(entity.name, texts);
                } : undefined}
                onUpdate={onUpdateObservation ? async (obsId, content) => {
                  await onUpdateObservation(obsId, entity.name, content);
                } : undefined}
                onDelete={onDeleteObservation ? async (obsId) => {
                  await onDeleteObservation(obsId, entity.name);
                } : undefined}
              />
            </div>

            {/* ── Danger zone ── */}
            {onDeleteEntity && (
              <div className="detail-danger-zone">
                <button
                  className={`panel-btn ${confirmDelete ? "panel-btn-danger" : ""}`}
                  onClick={() => void handleDelete()}
                  disabled={deleting}
                  style={{ width: "100%", justifyContent: "center", gap: 6 }}
                >
                  <IconTrash />
                  {deleting ? "Deleting..." : confirmDelete ? "Click again to confirm" : "Delete Entity"}
                </button>
                {confirmDelete && (
                  <button
                    className="panel-btn"
                    onClick={() => setConfirmDelete(false)}
                    style={{ width: "100%", justifyContent: "center", marginTop: 4, fontSize: 10 }}
                  >
                    Cancel
                  </button>
                )}
                {feedback && (
                  <div style={{ fontSize: 11, color: "var(--color-danger)", marginTop: 6, textAlign: "center" }}>
                    {feedback}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════
// Inline editable components
// ═══════════════════════════════════════════════════════════════════════

/** Single-line inline editable text field */
function EditableField({ value, placeholder, className, onSave }: {
  value: string; placeholder: string; className?: string;
  onSave: (v: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setDraft(value); }, [value]);
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const save = useCallback(async () => {
    if (!draft.trim() || draft.trim() === value) { setEditing(false); return; }
    setSaving(true);
    try { await onSave(draft.trim()); } finally { setSaving(false); setEditing(false); }
  }, [draft, value, onSave]);

  if (editing) {
    return (
      <div className="detail-inline-edit">
        <input
          ref={inputRef}
          className="detail-inline-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); if (e.key === "Escape") setEditing(false); }}
          disabled={saving}
          placeholder={placeholder}
        />
        <TinyBtn onClick={() => void save()} title="Save" color="var(--color-success)"><IconCheck /></TinyBtn>
        <TinyBtn onClick={() => setEditing(false)} title="Cancel"><IconCancel /></TinyBtn>
      </div>
    );
  }

  return (
    <div className={`detail-editable-display ${className || ""}`} onClick={() => setEditing(true)} title="Click to edit">
      {value || <span style={{ color: "var(--color-text-muted)" }}>{placeholder}</span>}
    </div>
  );
}

/** Type badge — click to edit */
function EditableTypeBadge({ type, onSave }: {
  type: string; onSave: (v: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(type);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setDraft(type); }, [type]);
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const save = useCallback(async () => {
    if (!draft.trim() || draft.trim() === type) { setEditing(false); return; }
    setSaving(true);
    try { await onSave(draft.trim()); } finally { setSaving(false); setEditing(false); }
  }, [draft, type, onSave]);

  const color = entityColor(type);

  if (editing) {
    return (
      <div className="detail-inline-edit" style={{ marginTop: 4 }}>
        <input
          ref={inputRef}
          className="detail-inline-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); if (e.key === "Escape") setEditing(false); }}
          disabled={saving}
          placeholder="Entity type"
          style={{ fontSize: 10, padding: "2px 6px" }}
        />
        <TinyBtn onClick={() => void save()} title="Save" color="var(--color-success)"><IconCheck /></TinyBtn>
        <TinyBtn onClick={() => setEditing(false)} title="Cancel"><IconCancel /></TinyBtn>
      </div>
    );
  }

  return (
    <span
      className="detail-type-badge"
      onClick={() => setEditing(true)}
      title="Click to change type"
      style={{
        background: color + "18",
        color,
        border: `1px solid ${color}33`,
        cursor: "pointer",
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />
      {type}
      <IconEdit />
    </span>
  );
}

/** Multi-line inline editable text area */
function EditableTextArea({ value, placeholder, onSave }: {
  value: string; placeholder: string; onSave: (v: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setDraft(value); }, [value]);
  useEffect(() => { if (editing && textRef.current) { textRef.current.focus(); textRef.current.setSelectionRange(textRef.current.value.length, textRef.current.value.length); } }, [editing]);

  const save = useCallback(async () => {
    if (draft === value) { setEditing(false); return; }
    setSaving(true);
    try { await onSave(draft); } finally { setSaving(false); setEditing(false); }
  }, [draft, value, onSave]);

  if (editing) {
    return (
      <div>
        <textarea
          ref={textRef}
          className="detail-inline-textarea"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") setEditing(false); }}
          disabled={saving}
          rows={3}
          placeholder="Enter description..."
        />
        <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
          <button className="panel-btn" onClick={() => void save()} disabled={saving} style={{ flex: 1, justifyContent: "center", gap: 4, fontSize: 10 }}>
            <IconCheck /> {saving ? "Saving..." : "Save"}
          </button>
          <button className="panel-btn" onClick={() => { setDraft(value); setEditing(false); }} style={{ flex: 1, justifyContent: "center", fontSize: 10 }}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="detail-editable-text"
      onClick={() => setEditing(true)}
      title="Click to edit"
    >
      {value
        ? <p style={{ fontSize: 12, lineHeight: 1.6, color: "var(--color-text-secondary)", margin: 0 }}>{value}</p>
        : <span style={{ fontSize: 12, color: "var(--color-text-muted)", fontStyle: "italic" }}>{placeholder}</span>
      }
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════
// Properties editor
// ═══════════════════════════════════════════════════════════════════════

function PropertiesEditor({ properties, onSave }: {
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
          <IconPlus /> Add property
        </button>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════
// Observations editor
// ═══════════════════════════════════════════════════════════════════════

function ObservationsEditor({ observations, entityName, allEntityNames, onNavigate, onAdd, onUpdate, onDelete }: {
  observations: EntityObservation[];
  entityName: string;
  allEntityNames?: string[];
  onNavigate: (name: string) => void;
  onAdd?: (texts: string[]) => Promise<void>;
  onUpdate?: (obsId: string, content: string) => Promise<void>;
  onDelete?: (obsId: string) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [newObs, setNewObs] = useState("");
  const [saving, setSaving] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (adding && textRef.current) textRef.current.focus(); }, [adding]);

  const handleAdd = useCallback(async () => {
    const lines = newObs.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length === 0 || !onAdd) return;
    setSaving(true);
    try { await onAdd(lines); setNewObs(""); setAdding(false); } finally { setSaving(false); }
  }, [newObs, onAdd]);

  return (
    <div className="detail-obs-list">
      {observations.map((obs, i) => (
        <ObservationCard
          key={obs.id || i}
          obs={obs}
          entityName={entityName}
          allEntityNames={allEntityNames}
          onNavigate={onNavigate}
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
              <IconPlus /> {saving ? "Adding..." : "Add"}
            </button>
            <button className="panel-btn" onClick={() => { setNewObs(""); setAdding(false); }} style={{ flex: 1, justifyContent: "center", fontSize: 10 }}>
              Cancel
            </button>
          </div>
        </div>
      ) : onAdd ? (
        <button className="detail-add-btn" onClick={() => setAdding(true)}>
          <IconPlus /> Add observation
        </button>
      ) : null}
    </div>
  );
}

function ObservationCard({ obs, entityName, allEntityNames, onNavigate, onUpdate, onDelete }: {
  obs: EntityObservation;
  entityName: string;
  allEntityNames?: string[];
  onNavigate: (name: string) => void;
  onUpdate?: (obsId: string, content: string) => Promise<void>;
  onDelete?: (obsId: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(obs.content);
  const [saving, setSaving] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setDraft(obs.content); }, [obs.content]);
  useEffect(() => { if (editing && textRef.current) { textRef.current.focus(); textRef.current.setSelectionRange(textRef.current.value.length, textRef.current.value.length); } }, [editing]);

  const save = useCallback(async () => {
    if (!draft.trim() || draft === obs.content || !onUpdate || !obs.id) { setEditing(false); return; }
    setSaving(true);
    try { await onUpdate(obs.id, draft.trim()); } finally { setSaving(false); setEditing(false); }
  }, [draft, obs, onUpdate]);

  const handleDelete = useCallback(async () => {
    if (!onDelete || !obs.id) return;
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
      <div className="detail-obs-content">
        {allEntityNames
          ? highlightEntities(obs.content, allEntityNames, entityName, onNavigate)
          : obs.content}
      </div>
      {obs.source && (
        <div className="detail-obs-source">{obs.source}</div>
      )}
      {(onUpdate || onDelete) && obs.id && (
        <div className="detail-obs-actions">
          {onUpdate && (
            <TinyBtn onClick={() => setEditing(true)} title="Edit observation"><IconEdit /></TinyBtn>
          )}
          {onDelete && (
            confirmDel ? (
              <>
                <TinyBtn onClick={() => void handleDelete()} title="Confirm delete" color="var(--color-danger)"><IconCheck /></TinyBtn>
                <TinyBtn onClick={() => setConfirmDel(false)} title="Cancel"><IconCancel /></TinyBtn>
              </>
            ) : (
              <TinyBtn onClick={() => void handleDelete()} title="Delete observation" color="var(--color-danger)"><IconTrash /></TinyBtn>
            )
          )}
        </div>
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


// ── Sub-components ──

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
