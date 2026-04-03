import { useEffect, useRef, useCallback, useState } from "react";
import { ForceEngine } from "../engine/ForceEngine";
import type { SimNode, PhysicsConfig } from "../engine/ForceEngine";
import { render, s2w } from "../engine/CanvasRenderer";
import type { ViewTransform, RenderState } from "../engine/CanvasRenderer";
import { entityColorPalette } from "../utils/colors";
import type { GraphResponse } from "../types/graph";

export interface GraphCanvasProps {
  graph: GraphResponse | null;
  visibleEntityTypes: Set<string>;
  selectedNodeId: string | null;
  physics: Partial<PhysicsConfig>;
  onSelectNode: (name: string) => void;
  onDeselectNode: () => void;
  onReheat?: () => void;
}

interface TooltipData {
  x: number;
  y: number;
  name: string;
  type: string;
  degree: number;
  neighbors: string[];
}

export default function GraphCanvas({
  graph,
  visibleEntityTypes,
  selectedNodeId,
  physics,
  onSelectNode,
  onDeselectNode,
}: GraphCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<ForceEngine>(new ForceEngine());
  const viewRef = useRef<ViewTransform>({ x: 0, y: 0, zoom: 1 });
  const rafRef = useRef<number>(0);
  const hoveredRef = useRef<SimNode | null>(null);
  const initialFitDoneRef = useRef(false);
  const anchorIdRef = useRef<string | null>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  // Drag state — separate clickStart (never mutated) from panLast (mutated for pan delta)
  const dragRef = useRef<{
    node: SimNode | null;
    panning: boolean;
    clickStartX: number;
    clickStartY: number;
    panLastX: number;
    panLastY: number;
  }>({ node: null, panning: false, clickStartX: 0, clickStartY: 0, panLastX: 0, panLastY: 0 });

  // Stable callback refs to avoid stale closures
  const onSelectRef = useRef(onSelectNode);
  const onDeselectRef = useRef(onDeselectNode);
  const selectedRef = useRef(selectedNodeId);
  onSelectRef.current = onSelectNode;
  onDeselectRef.current = onDeselectNode;
  selectedRef.current = selectedNodeId;

  // ── Build simulation graph when data changes ──
  useEffect(() => {
    const engine = engineRef.current;

    // Preserve existing node positions so graph rebuilds don't randomize everything
    const oldPositions = new Map<string, { x: number; y: number; pinned: boolean }>();
    for (const n of engine.nodes) {
      oldPositions.set(n.id, { x: n.x, y: n.y, pinned: n.pinned });
    }

    engine.clear();
    if (!graph) return;

    const visibleEntities = graph.entities.filter((e) =>
      visibleEntityTypes.has(e.entity_type),
    );
    const nameSet = new Set(visibleEntities.map((e) => e.name));

    for (const e of visibleEntities) {
      const palette = entityColorPalette(e.entity_type);
      const old = oldPositions.get(e.name);
      engine.addNode({
        id: e.name,
        label: e.name,
        entityType: e.entity_type,
        x: old ? old.x : (Math.random() - 0.5) * 300,
        y: old ? old.y : (Math.random() - 0.5) * 300,
        vx: 0,
        vy: 0,
        ax: 0,
        ay: 0,
        degree: 0,
        pinned: old ? old.pinned : false,
        color: palette.stroke,
        glowColor: palette.glow,
        fillColor: palette.fill,
        strokeColor: palette.stroke,
      });
    }

    for (const r of graph.relationships) {
      if (nameSet.has(r.source) && nameSet.has(r.target)) {
        engine.addEdge({
          source: r.source,
          target: r.target,
          label: r.relationship_type,
          weight: r.weight,
        });
      }
    }

    // ── Anchor the most-connected node at origin ──
    // This stabilizes the coordinate system: the highest-degree node sits at (0,0),
    // all other nodes settle relative to it via physics, and handleCenter re-centers on it.
    if (engine.nodes.length > 0) {
      let anchorIdx = 0;
      let maxDeg = engine.nodes[0]!.degree;
      for (let i = 1; i < engine.nodes.length; i++) {
        if (engine.nodes[i]!.degree > maxDeg) {
          maxDeg = engine.nodes[i]!.degree;
          anchorIdx = i;
        }
      }
      const anchor = engine.nodes[anchorIdx]!;
      anchor.x = 0;
      anchor.y = 0;
      anchor.vx = 0;
      anchor.vy = 0;
      anchor.pinned = true;
      anchorIdRef.current = anchor.id;
    }

    engine.reheat();

    // Only auto-fit on first load; subsequent changes preserve current view
    if (!initialFitDoneRef.current) {
      initialFitDoneRef.current = true;
      setTimeout(() => {
        fitToView();
      }, 600);
    }
  }, [graph, visibleEntityTypes]);

  // ── Update physics config ──
  useEffect(() => {
    Object.assign(engineRef.current.config, physics);
  }, [physics]);

  // ── Canvas resize ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas!.getBoundingClientRect();
      canvas!.width = rect.width * dpr;
      canvas!.height = rect.height * dpr;
    }

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, []);

  // ── Animation loop ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;

    function frame() {
      const engine = engineRef.current;
      engine.tick();

      const state: RenderState = {
        hoveredNode: hoveredRef.current,
        selectedNode: selectedRef.current,
        adj: engine.adj,
      };

      render(
        ctx,
        engine.nodes,
        engine.edges,
        viewRef.current,
        state,
        engine["idxMap"] as Map<string, number>,
      );

      rafRef.current = requestAnimationFrame(frame);
    }

    rafRef.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  // ── Passive wheel listener (zoom) ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    function onWheel(e: WheelEvent) {
      e.preventDefault();
      const view = viewRef.current;
      const rect = canvas!.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;

      // World position under mouse before zoom
      const w = s2w(mx, my, view, rect.width, rect.height);

      const factor = e.deltaY > 0 ? 0.88 : 1.14;
      view.zoom = Math.max(0.05, Math.min(8, view.zoom * factor));

      // Keep world position under mouse stable
      const after = s2w(mx, my, view, rect.width, rect.height);
      view.x += after.x - w.x;
      view.y += after.y - w.y;
    }

    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  // ── Mouse interactions ──
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const view = viewRef.current;
    const world = s2w(mx, my, view, rect.width, rect.height);
    const engine = engineRef.current;
    const hit = engine.nodeAt(world.x, world.y, view.zoom);

    if (hit) {
      // Start dragging a node
      hit.pinned = true;
      dragRef.current = {
        node: hit,
        panning: false,
        clickStartX: mx,
        clickStartY: my,
        panLastX: mx,
        panLastY: my,
      };
      canvas.style.cursor = "grabbing";
    } else {
      // Start panning
      dragRef.current = {
        node: null,
        panning: true,
        clickStartX: mx,
        clickStartY: my,
        panLastX: mx,
        panLastY: my,
      };
      canvas.style.cursor = "grabbing";
    }
    // Hide tooltip during interaction
    setTooltip(null);
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const view = viewRef.current;
    const drag = dragRef.current;

    if (drag.node) {
      // Dragging a node — update position in world coords
      const world = s2w(mx, my, view, rect.width, rect.height);
      drag.node.x = world.x;
      drag.node.y = world.y;
      drag.node.vx = 0;
      drag.node.vy = 0;
      // Warm alpha to keep simulation alive during drag (from reference)
      engineRef.current.alpha = Math.max(engineRef.current.alpha, 0.25);
    } else if (drag.panning) {
      // Panning
      const dx = mx - drag.panLastX;
      const dy = my - drag.panLastY;
      view.x += dx / view.zoom;
      view.y += dy / view.zoom;
      drag.panLastX = mx;
      drag.panLastY = my;
    } else {
      // Hover detection
      const world = s2w(mx, my, view, rect.width, rect.height);
      const hit = engineRef.current.nodeAt(world.x, world.y, view.zoom);
      hoveredRef.current = hit;
      canvas.style.cursor = hit ? "pointer" : "default";

      // Tooltip
      if (hit) {
        const neighbors = [...(engineRef.current.adj.get(hit.id) ?? [])];
        setTooltip({
          x: mx + 14,
          y: my - 10,
          name: hit.label,
          type: hit.entityType,
          degree: hit.degree,
          neighbors: neighbors.slice(0, 5),
        });
      } else {
        setTooltip(null);
      }
    }
  }, []);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const drag = dragRef.current;

    // Use clickStart (never mutated) for click detection
    const dist = Math.sqrt(
      (mx - drag.clickStartX) ** 2 + (my - drag.clickStartY) ** 2,
    );

    if (drag.node) {
      // Release node pin — but keep anchor node pinned
      if (drag.node.id !== anchorIdRef.current) {
        drag.node.pinned = false;
      }

      // Click (not drag) → select
      if (dist < 4) {
        onSelectRef.current(drag.node.id);
      }
    } else if (drag.panning && dist < 4) {
      // Click on empty canvas → deselect
      onDeselectRef.current();
    }

    canvas.style.cursor = "default";
    dragRef.current = { node: null, panning: false, clickStartX: 0, clickStartY: 0, panLastX: 0, panLastY: 0 };
  }, []);

  const handleMouseLeave = useCallback(() => {
    hoveredRef.current = null;
    setTooltip(null);
    const drag = dragRef.current;
    if (drag.node) {
      if (drag.node.id !== anchorIdRef.current) {
        drag.node.pinned = false;
      }
    }
    dragRef.current = { node: null, panning: false, clickStartX: 0, clickStartY: 0, panLastX: 0, panLastY: 0 };
    const canvas = canvasRef.current;
    if (canvas) canvas.style.cursor = "default";
  }, []);

  // ── Double-click to toggle pin ──
  const handleDoubleClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const view = viewRef.current;
    const world = s2w(mx, my, view, rect.width, rect.height);
    const hit = engineRef.current.nodeAt(world.x, world.y, view.zoom);
    if (hit) {
      hit.pinned = !hit.pinned;
    }
  }, []);

  // ── Fit to view ──
  const fitToView = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const engine = engineRef.current;
    if (engine.nodes.length === 0) return;

    const rect = canvas.getBoundingClientRect();
    const b = engine.bounds();
    const pad = 80;
    const spanX = (b.maxX - b.minX) || 200;
    const spanY = (b.maxY - b.minY) || 200;
    const zoom = Math.min(
      (rect.width - pad * 2) / spanX,
      (rect.height - pad * 2) / spanY,
      2,
    );

    viewRef.current = {
      x: -(b.minX + b.maxX) / 2,
      y: -(b.minY + b.maxY) / 2,
      zoom: Math.max(0.1, zoom),
    };
  }, []);

  // ── HUD actions ──
  const handleZoomIn = useCallback(() => {
    viewRef.current.zoom = Math.min(8, viewRef.current.zoom * 1.3);
  }, []);

  const handleZoomOut = useCallback(() => {
    viewRef.current.zoom = Math.max(0.05, viewRef.current.zoom * 0.7);
  }, []);

  const handleCenter = useCallback(() => {
    viewRef.current.x = 0;
    viewRef.current.y = 0;
    viewRef.current.zoom = 1;
  }, []);

  const handleReheat = useCallback(() => {
    engineRef.current.reheat();
  }, []);

  // ── Keyboard shortcuts ──
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Don't capture keys when typing in inputs
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }

      switch (e.key) {
        case " ":
          e.preventDefault();
          engineRef.current.reheat();
          break;
        case "f":
        case "F":
          e.preventDefault();
          fitToView();
          break;
        case "Escape":
          onDeselectRef.current();
          break;
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitToView]);

  // Build neighbor extra text for tooltip
  const ttExtra = tooltip && tooltip.neighbors.length > 0
    ? tooltip.neighbors.join(", ")
    : "";
  const ttNeighborCount = tooltip
    ? (engineRef.current.adj.get(tooltip.name)?.size ?? 0)
    : 0;
  const ttMoreCount = ttNeighborCount > 5 ? ttNeighborCount - 5 : 0;

  return (
    <div className="graph-canvas-wrap">
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: "100%", display: "block" }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onDoubleClick={handleDoubleClick}
      />

      {/* Tooltip */}
      {tooltip && (
        <div
          className="canvas-tooltip on"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="canvas-tooltip-name">{tooltip.name}</div>
          <div className="canvas-tooltip-meta">
            {tooltip.type} &middot; Degree: {tooltip.degree}
          </div>
          {ttExtra && (
            <div className="canvas-tooltip-nbrs">
              &harr; {ttExtra}{ttMoreCount > 0 ? ` +${ttMoreCount} more` : ""}
            </div>
          )}
        </div>
      )}

      {/* HUD Controls — bottom right */}
      <div className="canvas-hud">
        <button className="canvas-hud-btn" onClick={handleZoomIn} title="Zoom in (+)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
        <button className="canvas-hud-btn" onClick={handleZoomOut} title="Zoom out (-)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
        <button className="canvas-hud-btn" onClick={fitToView} title="Fit to view (F)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
          </svg>
        </button>
        <button className="canvas-hud-btn" onClick={handleCenter} title="Reset view">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" /><path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
          </svg>
        </button>
        <button className="canvas-hud-btn" onClick={handleReheat} title="Reheat (Space)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M23 4v6h-6M1 20v-6h6" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
        </button>
      </div>
    </div>
  );
}
