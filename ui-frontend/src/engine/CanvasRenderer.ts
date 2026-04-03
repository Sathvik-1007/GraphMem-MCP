// ─────────────────────────────────────────────────────────────
// CanvasRenderer — Canvas2D rendering for force-directed graphs
//
// Renders: grid background, gradient edges, glow-halo nodes,
//          labels, hover state, pin indicators.
//
// Ported from graph-visualizer.html with entity-type-based colors.
// ─────────────────────────────────────────────────────────────

import type { SimNode, SimEdge } from "./ForceEngine";
import { nodeRadius } from "./ForceEngine";
import { hexToLightFill } from "../utils/colors";

export interface ViewTransform {
  /** Pan offset X (world coords) */
  x: number;
  /** Pan offset Y (world coords) */
  y: number;
  /** Zoom level */
  zoom: number;
}

export interface RenderState {
  hoveredNode: SimNode | null;
  selectedNode: string | null;
  adj: Map<string, Set<string>>;
}

/** World → screen coordinate transform */
export function w2s(
  wx: number,
  wy: number,
  view: ViewTransform,
  W: number,
  H: number,
): { x: number; y: number } {
  return {
    x: (wx + view.x) * view.zoom + W / 2,
    y: (wy + view.y) * view.zoom + H / 2,
  };
}

/** Screen → world coordinate transform */
export function s2w(
  sx: number,
  sy: number,
  view: ViewTransform,
  W: number,
  H: number,
): { x: number; y: number } {
  return {
    x: (sx - W / 2) / view.zoom - view.x,
    y: (sy - H / 2) / view.zoom - view.y,
  };
}

/** Read a CSS custom property from :root */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Detect if current theme is light */
function isLightTheme(): boolean {
  return document.documentElement.getAttribute("data-theme") === "light";
}

/** Main render function — called every animation frame */
export function render(
  ctx: CanvasRenderingContext2D,
  nodes: SimNode[],
  edges: SimEdge[],
  view: ViewTransform,
  state: RenderState,
  nodeIndex: Map<string, number>,
): void {
  const dpr = window.devicePixelRatio || 1;
  // Use CSS pixel dimensions (not DPR-scaled canvas.width/height)
  const W = ctx.canvas.width / dpr;
  const H = ctx.canvas.height / dpr;

  // Reset transform then apply DPR scale
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Clear + background (theme-aware)
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = cssVar("--canvas-bg") || "#0a0a0b";
  ctx.fillRect(0, 0, W, H);

  // Subtle grid
  drawGrid(ctx, W, H, view);

  // World-space transform
  ctx.save();
  ctx.translate(W / 2, H / 2);
  ctx.scale(view.zoom, view.zoom);
  ctx.translate(view.x, view.y);

  const light = isLightTheme();
  drawEdges(ctx, nodes, edges, view, state, nodeIndex, light);
  drawNodes(ctx, nodes, view, state, light);

  ctx.restore();
}

// ── Grid ──────────────────────────────────────────────

function drawGrid(
  ctx: CanvasRenderingContext2D,
  W: number,
  H: number,
  view: ViewTransform,
): void {
  const sz = 44 * view.zoom;
  if (sz < 4) return; // Don't draw when zoomed out too far

  const ox = (((view.x * view.zoom) % sz) + W / 2) % sz;
  const oy = (((view.y * view.zoom) % sz) + H / 2) % sz;

  ctx.strokeStyle = cssVar("--canvas-grid") || "rgba(255,255,255,0.025)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = ox; x < W; x += sz) {
    ctx.moveTo(x, 0);
    ctx.lineTo(x, H);
  }
  for (let y = oy; y < H; y += sz) {
    ctx.moveTo(0, y);
    ctx.lineTo(W, y);
  }
  ctx.stroke();
}

// ── Edges ─────────────────────────────────────────────

function drawEdges(
  ctx: CanvasRenderingContext2D,
  nodes: SimNode[],
  edges: SimEdge[],
  view: ViewTransform,
  state: RenderState,
  nodeIndex: Map<string, number>,
  light: boolean,
): void {
  const { hoveredNode, selectedNode, adj } = state;
  const hlNeighbors = hoveredNode ? adj.get(hoveredNode.id) ?? null : null;
  const selNeighbors = selectedNode ? adj.get(selectedNode) ?? null : null;

  for (const e of edges) {
    const ai = nodeIndex.get(e.source);
    const bi = nodeIndex.get(e.target);
    if (ai === undefined || bi === undefined) continue;

    const a = nodes[ai]!;
    const b = nodes[bi]!;

    // Determine highlight state
    const isHoverHL =
      hlNeighbors !== null &&
      (a.id === hoveredNode!.id ||
        b.id === hoveredNode!.id ||
        hlNeighbors.has(a.id) ||
        hlNeighbors.has(b.id));

    const isSelHL =
      selNeighbors !== null &&
      (a.id === selectedNode || b.id === selectedNode);

    const isHL = isHoverHL || isSelHL;

    // Dim non-selected edges when a node is selected
    const isDimmed =
      selectedNode !== null && !isSelHL;

    // Gradient edge color
    const grd = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
    if (light) {
      // Light mode: fully opaque solid edges
      const alphaHL = isDimmed ? "30" : isHL ? "dd" : "aa";
      grd.addColorStop(0, a.strokeColor + alphaHL);
      grd.addColorStop(1, b.strokeColor + alphaHL);
      ctx.strokeStyle = grd;
      ctx.lineWidth = (isHL ? 2.0 : 1.2) / view.zoom;
      ctx.globalAlpha = isDimmed ? 0.3 : 1.0;
    } else {
      // Dark mode: original translucent edges
      const alphaHex = isDimmed ? "18" : isHL ? "bb" : "55";
      grd.addColorStop(0, a.strokeColor + alphaHex);
      grd.addColorStop(1, b.strokeColor + alphaHex);
      ctx.strokeStyle = grd;
      ctx.lineWidth = (isHL ? 2.0 : 1.0) / view.zoom;
      ctx.globalAlpha = isDimmed ? 0.25 : isHL ? 0.95 : 0.6;
    }
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

// ── Nodes ─────────────────────────────────────────────

function drawNodes(
  ctx: CanvasRenderingContext2D,
  nodes: SimNode[],
  view: ViewTransform,
  state: RenderState,
  light: boolean,
): void {
  const { hoveredNode, selectedNode } = state;
  const labelColor = cssVar("--canvas-label") || "rgba(200,199,196,0.7)";
  const labelActiveColor = cssVar("--canvas-label-active") || "#ffffff";

  // Draw lowest-degree nodes first so hubs appear on top
  const sorted = [...nodes].sort((a, b) => a.degree - b.degree);
  const maxDegree = sorted.length > 0 ? sorted[sorted.length - 1]!.degree : 0;

  for (const nd of sorted) {
    const r = nodeRadius(nd.degree);
    const isHovered = hoveredNode !== null && hoveredNode.id === nd.id;
    const isSelected = selectedNode !== null && selectedNode === nd.id;
    const isDimmed =
      selectedNode !== null &&
      selectedNode !== nd.id &&
      !(state.adj.get(selectedNode)?.has(nd.id) ?? false);

    if (isDimmed) {
      ctx.globalAlpha = 0.2;
    }

    const glowR = r * (isHovered || isSelected ? 4 : nd.degree > 1 ? 2.8 : 1.5);

    // Light mode: colored halo border instead of shadow/glow
    // Dark mode: radial glow halo
    if (light) {
      // No radial gradient shadow — just skip the glow in light mode
    } else if (nd.degree > 0 || isHovered || isSelected) {
      const grd = ctx.createRadialGradient(nd.x, nd.y, r * 0.3, nd.x, nd.y, glowR);
      grd.addColorStop(0, nd.glowColor);
      grd.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(nd.x, nd.y, glowR, 0, Math.PI * 2);
      ctx.fill();
    }

    // Body — light mode uses pastel fill with colored border
    ctx.fillStyle = light ? hexToLightFill(nd.strokeColor) : nd.fillColor;
    if (light) {
      // Light mode: light border default, heavy colored border on hover/select
      ctx.strokeStyle = nd.strokeColor;
      ctx.lineWidth = ((isHovered || isSelected) ? 3.0 : 1.2) / view.zoom;
      if (isHovered || isSelected) {
        ctx.globalAlpha = isDimmed ? 0.2 : 1;
      }
    } else {
      ctx.strokeStyle = isHovered ? labelActiveColor : isSelected ? labelActiveColor : nd.strokeColor;
      ctx.lineWidth = ((isHovered || isSelected) ? 2.5 : 1.5) / view.zoom;
    }
    ctx.beginPath();
    ctx.arc(nd.x, nd.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Pin dot
    if (nd.pinned) {
      ctx.fillStyle = "#fdab43";
      ctx.beginPath();
      ctx.arc(nd.x + r * 0.65, nd.y - r * 0.65, 2.5 / view.zoom, 0, Math.PI * 2);
      ctx.fill();
    }

    // Label — progressive visibility based on zoom and node degree
    // zoom >= 0.55: all labels visible (comfortable scroll range)
    // zoom < 0.55:  only nodes with degree >= threshold show labels
    //               threshold rises as zoom drops, but the biggest node always shows
    // Hovered/selected always show
    const showLabel = (() => {
      if (isHovered || isSelected) return true;
      if (view.zoom >= 0.55) return true;
      if (maxDegree === 0) return view.zoom > 0.15;
      // Map zoom [0.05..0.55] → threshold [maxDegree..0]
      const t = Math.max(0, Math.min(1, (0.55 - view.zoom) / 0.5));
      const degreeThreshold = t * maxDegree;
      // The node with maxDegree always passes (degreeThreshold at most = maxDegree)
      return nd.degree >= degreeThreshold;
    })();
    if (showLabel) {
      const fs = Math.max(9, 11 / view.zoom);
      ctx.font = `${isHovered || isSelected ? 600 : 400} ${fs}px Inter,system-ui,sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = isHovered || isSelected ? labelActiveColor : labelColor;
      ctx.fillText(nd.label, nd.x, nd.y + r + 10 / view.zoom);
    }

    if (isDimmed) {
      ctx.globalAlpha = 1;
    }
  }
}
