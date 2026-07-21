// ─────────────────────────────────────────────────────────────
// ForceEngine — Pure physics simulation for force-directed graphs
//
// Physics model (ported from graph-visualizer.html):
//   Repulsion  →  Coulomb:  F = k_r · q_i·q_j / r²   (pushes apart)
//   Attraction →  Hooke:    F = k_s · (r − L₀)        (pulls along edge)
//   Gravity    →  Linear:   F = k_g · dist_center      (prevents drift)
//   Integration→  Euler + velocity damping + simulated annealing
//
// Repulsion is summed with a Barnes-Hut quadtree (O(n log n)) rather than an
// exact all-pairs sweep (O(n²)); see THETA below.
//
// Zero dependencies. No DOM. No React. Pure math.
// ─────────────────────────────────────────────────────────────

export interface SimNode {
  id: string;
  label: string;
  entityType: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  ax: number;
  ay: number;
  degree: number;
  pinned: boolean;
  /** Extra data the renderer may use (color, etc.) */
  color: string;
  glowColor: string;
  fillColor: string;
  strokeColor: string;
}

export interface SimEdge {
  source: string; // node id
  target: string; // node id
  label: string;
  weight: number;
}

export interface PhysicsConfig {
  repulsion: number;
  springLen: number;
  springStr: number;
  gravity: number;
  damping: number;
  maxVelocity: number;
  massBase: number;
  massPerDeg: number;
}

export const DEFAULT_PHYSICS: PhysicsConfig = {
  repulsion: 5000,
  springLen: 120,
  springStr: 0.08,
  gravity: 0.012,
  damping: 0.85,
  maxVelocity: 18,
  massBase: 1.0,
  massPerDeg: 0.4,
};

/**
 * Barnes-Hut opening angle. A quadtree cell is aggregated into a single body
 * when cellWidth / distanceToCell < THETA; otherwise the cell is opened and its
 * children are visited.
 *
 * 0.9 is deliberately loose. This layout is judged by eye, not by conserved
 * energy: at 0.9 the settled positions are visually indistinguishable from the
 * exact sum, while far fewer cells get opened per node. Lowering it toward 0.5
 * buys accuracy nobody can see for roughly double the work; THETA = 0 degenerates
 * back to the exact O(n²) all-pairs sum.
 */
const THETA = 0.9;
const THETA_SQ = THETA * THETA;

/**
 * Quadtree subdivision cap. Each level halves the cell, so 32 levels shrink the
 * root box by 2^32 — past that, two bodies still sharing a cell are at
 * effectively identical coordinates. They then share a leaf and get separated by
 * the coincidence jitter in the force loop. This cap is also what makes
 * insertBody() terminate on exactly-coincident nodes.
 */
const MAX_QUAD_DEPTH = 32;

/** Compute visual radius from degree — linear for clear differentiation */
export function nodeRadius(degree: number): number {
  return Math.max(6, Math.min(32, 5 + degree * 2.5));
}

/** Compute mass from degree (heavier nodes move less) */
function nodeMass(cfg: PhysicsConfig, degree: number): number {
  return cfg.massBase + degree * cfg.massPerDeg;
}

export class ForceEngine {
  nodes: SimNode[] = [];
  edges: SimEdge[] = [];

  /** Adjacency: node id → Set<node id> */
  adj: Map<string, Set<string>> = new Map();

  /** Fast ID→index lookup */
  private idxMap: Map<string, number> = new Map();

  /**
   * Canonical (unordered) keys of edges already added — O(1) dedup on insert.
   * The linear scan this replaced rebuilt a sorted-join key for every existing
   * edge on every insert: O(E²) time and 2 array+string allocations per compare.
   */
  private edgeKeys: Set<string> = new Set();

  /** Cache backing the `nodesByDegree` getter. */
  private sortedByDegree: SimNode[] = [];
  private sortedDirty = true;

  /** Simulated annealing temperature [0..1] */
  alpha = 1.0;

  /** Is the simulation running? */
  running = true;

  config: PhysicsConfig;

  constructor(config?: Partial<PhysicsConfig>) {
    this.config = { ...DEFAULT_PHYSICS, ...config };
  }

  // ── Graph building ────────────────────────────────────

  clear(): void {
    this.nodes = [];
    this.edges = [];
    this.adj.clear();
    this.idxMap.clear();
    this.edgeKeys.clear();
    this.sortedDirty = true;
    this.alpha = 1.0;
  }

  addNode(node: SimNode): void {
    this.idxMap.set(node.id, this.nodes.length);
    this.nodes.push(node);
    if (!this.adj.has(node.id)) {
      this.adj.set(node.id, new Set());
    }
    this.sortedDirty = true;
  }

  addEdge(edge: SimEdge): void {
    // Deduplicate — unordered pair, so a↔b and b↔a collapse to one edge.
    const key =
      edge.source < edge.target
        ? `${edge.source}\0${edge.target}`
        : `${edge.target}\0${edge.source}`;
    if (this.edgeKeys.has(key)) return;
    this.edgeKeys.add(key);
    this.edges.push(edge);
    this.sortedDirty = true;

    // Update adjacency
    if (!this.adj.has(edge.source)) this.adj.set(edge.source, new Set());
    if (!this.adj.has(edge.target)) this.adj.set(edge.target, new Set());
    this.adj.get(edge.source)!.add(edge.target);
    this.adj.get(edge.target)!.add(edge.source);

    // Update degrees
    const si = this.idxMap.get(edge.source);
    const ti = this.idxMap.get(edge.target);
    if (si !== undefined) this.nodes[si]!.degree++;
    if (ti !== undefined) this.nodes[ti]!.degree++;
  }

  getNode(id: string): SimNode | undefined {
    const idx = this.idxMap.get(id);
    return idx !== undefined ? this.nodes[idx] : undefined;
  }

  /** Read-only ID→index lookup, for renderers that resolve edge endpoints. */
  get nodeIndex(): ReadonlyMap<string, number> {
    return this.idxMap;
  }

  /**
   * Nodes ordered by ascending degree, so a painter draws hubs last.
   * Cached: the sort runs when the graph changes, not once per frame.
   */
  get nodesByDegree(): readonly SimNode[] {
    if (this.sortedDirty) {
      this.sortedByDegree = [...this.nodes].sort((a, b) => a.degree - b.degree);
      this.sortedDirty = false;
    }
    return this.sortedByDegree;
  }

  // ── Barnes-Hut quadtree ───────────────────────────────
  //
  // Flat typed arrays, reused across ticks: a per-frame object-per-cell tree
  // would hand the GC thousands of short-lived objects 60 times a second.
  // Cell c occupies index c in every array below, and slots 4c..4c+3 of qChild.

  private qCap = 0;
  private qCount = 0;
  private qChild = new Int32Array(0); // 4 child cell indices per cell, -1 = absent
  private qBody = new Int32Array(0); // body index if this cell is a leaf, else -1
  private qCx = new Float64Array(0); // cell centre
  private qCy = new Float64Array(0);
  private qHalf = new Float64Array(0); // half the cell width
  private qCharge = new Float64Array(0); // Σ charge of contained bodies
  private qComX = new Float64Array(0); // charge-weighted centre of mass
  private qComY = new Float64Array(0);
  private qStack = new Int32Array(0); // explicit traversal stack (no recursion)
  private charges = new Float64Array(0); // per-node charge, indexed like `nodes`

  private ensureQuadCapacity(cells: number): void {
    if (cells <= this.qCap) return;
    let cap = Math.max(64, this.qCap);
    while (cap < cells) cap *= 2;

    const child = new Int32Array(cap * 4);
    child.set(this.qChild);
    const body = new Int32Array(cap);
    body.set(this.qBody);
    const cx = new Float64Array(cap);
    cx.set(this.qCx);
    const cy = new Float64Array(cap);
    cy.set(this.qCy);
    const half = new Float64Array(cap);
    half.set(this.qHalf);
    const charge = new Float64Array(cap);
    charge.set(this.qCharge);
    const comX = new Float64Array(cap);
    comX.set(this.qComX);
    const comY = new Float64Array(cap);
    comY.set(this.qComY);

    this.qChild = child;
    this.qBody = body;
    this.qCx = cx;
    this.qCy = cy;
    this.qHalf = half;
    this.qCharge = charge;
    this.qComX = comX;
    this.qComY = comY;
    this.qCap = cap;
  }

  /** Allocate an empty cell and return its index. */
  private newCell(cx: number, cy: number, half: number): number {
    const c = this.qCount++;
    this.ensureQuadCapacity(this.qCount);
    this.qCx[c] = cx;
    this.qCy[c] = cy;
    this.qHalf[c] = half;
    this.qCharge[c] = 0;
    this.qComX[c] = 0;
    this.qComY[c] = 0;
    this.qBody[c] = -1;
    const base = c * 4;
    this.qChild[base] = -1;
    this.qChild[base + 1] = -1;
    this.qChild[base + 2] = -1;
    this.qChild[base + 3] = -1;
    return c;
  }

  /** Index of the child cell of `cell` containing (x, y), creating it if absent. */
  private descend(cell: number, x: number, y: number): number {
    const cx = this.qCx[cell]!;
    const cy = this.qCy[cell]!;
    const quad = (x >= cx ? 1 : 0) + (y >= cy ? 2 : 0);
    const slot = cell * 4 + quad;
    const existing = this.qChild[slot]!;
    if (existing >= 0) return existing;
    const h = this.qHalf[cell]! / 2;
    const created = this.newCell(
      quad & 1 ? cx + h : cx - h,
      quad & 2 ? cy + h : cy - h,
      h,
    );
    this.qChild[slot] = created;
    return created;
  }

  private insertBody(i: number): void {
    const nd = this.nodes[i]!;
    const x = nd.x;
    const y = nd.y;
    const q = this.charges[i]!;

    let cell = 0;
    for (let depth = 0; ; depth++) {
      // Every cell on the path accumulates this body's charge and weighted position.
      this.qCharge[cell] = this.qCharge[cell]! + q;
      this.qComX[cell] = this.qComX[cell]! + q * x;
      this.qComY[cell] = this.qComY[cell]! + q * y;

      const occupant = this.qBody[cell]!;
      if (occupant === -1) {
        // Internal cell, or an empty leaf. An empty leaf has no children.
        if (this.qChild[cell * 4]! < 0 &&
            this.qChild[cell * 4 + 1]! < 0 &&
            this.qChild[cell * 4 + 2]! < 0 &&
            this.qChild[cell * 4 + 3]! < 0) {
          this.qBody[cell] = i;
          return;
        }
      } else {
        // Occupied leaf. Past MAX_QUAD_DEPTH the two bodies are coincident:
        // leave the leaf pointing at the first one and let their charges merge.
        if (depth >= MAX_QUAD_DEPTH) return;
        const other = this.nodes[occupant]!;
        this.qBody[cell] = -1;
        const moved = this.descend(cell, other.x, other.y);
        const oq = this.charges[occupant]!;
        this.qCharge[moved] = this.qCharge[moved]! + oq;
        this.qComX[moved] = this.qComX[moved]! + oq * other.x;
        this.qComY[moved] = this.qComY[moved]! + oq * other.y;
        this.qBody[moved] = occupant;
      }
      cell = this.descend(cell, x, y);
    }
  }

  private buildQuadtree(): void {
    const nodes = this.nodes;
    const n = nodes.length;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (let i = 0; i < n; i++) {
      const nd = nodes[i]!;
      if (nd.x < minX) minX = nd.x;
      if (nd.x > maxX) maxX = nd.x;
      if (nd.y < minY) minY = nd.y;
      if (nd.y > maxY) maxY = nd.y;
    }
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    // Pad so the extreme coordinates land strictly inside the root box.
    const half = Math.max(maxX - cx, maxY - cy, 1) * 1.001;

    this.qCount = 0;
    this.newCell(cx, cy, half);
    for (let i = 0; i < n; i++) this.insertBody(i);

    // Weighted sums → actual centres of mass.
    for (let c = 0; c < this.qCount; c++) {
      const q = this.qCharge[c]!;
      if (q > 0) {
        this.qComX[c] = this.qComX[c]! / q;
        this.qComY[c] = this.qComY[c]! / q;
      }
    }
  }

  /**
   * Coulomb repulsion via Barnes-Hut: build the tree once (O(n log n)), then walk
   * it per node, collapsing distant subtrees into one aggregated body.
   */
  private applyRepulsion(): void {
    const nodes = this.nodes;
    const n = nodes.length;
    if (n < 2) return;

    if (this.charges.length < n) this.charges = new Float64Array(n * 2);
    for (let i = 0; i < n; i++) {
      this.charges[i] = nodeRadius(nodes[i]!.degree);
    }

    this.buildQuadtree();

    // A pop pushes at most 4 cells and the tree has qCount cells, so the stack
    // never exceeds qCount + 3 entries.
    if (this.qStack.length < this.qCount + 4) {
      this.qStack = new Int32Array((this.qCount + 4) * 2);
    }

    const { qChild, qBody, qCharge, qComX, qComY, qHalf, qStack } = this;
    const cfg = this.config;
    const kr = cfg.repulsion;

    for (let i = 0; i < n; i++) {
      const a = nodes[i]!;
      const qa = this.charges[i]!;
      const ax = a.x;
      const ay = a.y;
      let fx = 0;
      let fy = 0;

      let sp = 0;
      qStack[sp++] = 0;
      while (sp > 0) {
        const c = qStack[--sp]!;
        const body = qBody[c]!;
        if (body === i) continue; // a node does not repel itself

        let dx = qComX[c]! - ax;
        let dy = qComY[c]! - ay;
        let d2 = dx * dx + dy * dy;

        if (body === -1) {
          // Internal cell: open it unless it is far enough to aggregate.
          // width/d < THETA  ⇔  width² < THETA²·d²  (avoids a sqrt on this path)
          const w = qHalf[c]! * 2;
          if (w * w >= THETA_SQ * d2) {
            const base = c * 4;
            const c0 = qChild[base]!;
            const c1 = qChild[base + 1]!;
            const c2 = qChild[base + 2]!;
            const c3 = qChild[base + 3]!;
            if (c0 >= 0) qStack[sp++] = c0;
            if (c1 >= 0) qStack[sp++] = c1;
            if (c2 >= 0) qStack[sp++] = c2;
            if (c3 >= 0) qStack[sp++] = c3;
            continue;
          }
        }

        let d = Math.sqrt(d2);
        if (d < 1) {
          // Coincident (or near-coincident) — nudge apart in a random direction.
          dx = Math.random() * 2 - 1;
          dy = Math.random() * 2 - 1;
          d = 1;
          d2 = 1;
        }
        const F = (kr * qa * qCharge[c]!) / d2;
        fx += (dx / d) * F;
        fy += (dy / d) * F;
      }

      const m = nodeMass(cfg, a.degree);
      a.ax -= fx / m;
      a.ay -= fy / m;
    }
  }

  // ── Simulation ────────────────────────────────────────

  /** One physics tick. Returns total kinetic energy. */
  tick(): number {
    if (!this.running || this.alpha < 8e-4) return 0;

    const { nodes, edges } = this;
    const cfg = this.config;

    // Reset acceleration
    for (const nd of nodes) {
      nd.ax = 0;
      nd.ay = 0;
    }

    // ── 1. Repulsion (Coulomb's law) ──
    // F_rep = k_r · (r_i · r_j) / d², summed with a Barnes-Hut quadtree.
    this.applyRepulsion();

    // ── 2. Spring attraction (Hooke's law) ──
    // F_spring = k_s · (d − L₀)
    for (const e of edges) {
      const ai = this.idxMap.get(e.source);
      const bi = this.idxMap.get(e.target);
      if (ai === undefined || bi === undefined) continue;

      const a = nodes[ai]!;
      const b = nodes[bi]!;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const stretch = d - cfg.springLen;
      const F = cfg.springStr * stretch;
      const fx = (dx / d) * F;
      const fy = (dy / d) * F;
      const mi = nodeMass(cfg, a.degree);
      const mj = nodeMass(cfg, b.degree);
      a.ax += fx / mi;
      a.ay += fy / mi;
      b.ax -= fx / mj;
      b.ay -= fy / mj;
    }

    // ── 3. Center gravity ──
    // F_grav = k_g · dist_from_center
    for (const nd of nodes) {
      nd.ax += -nd.x * cfg.gravity;
      nd.ay += -nd.y * cfg.gravity;
    }

    // ── 4. Euler integration + velocity damping ──
    let totalEnergy = 0;
    for (const nd of nodes) {
      if (nd.pinned) {
        nd.vx = 0;
        nd.vy = 0;
        continue;
      }
      nd.vx = (nd.vx + nd.ax) * cfg.damping;
      nd.vy = (nd.vy + nd.ay) * cfg.damping;
      const spd = Math.sqrt(nd.vx * nd.vx + nd.vy * nd.vy);
      if (spd > cfg.maxVelocity) {
        nd.vx *= cfg.maxVelocity / spd;
        nd.vy *= cfg.maxVelocity / spd;
      }
      nd.x += nd.vx;
      nd.y += nd.vy;
      totalEnergy += spd * spd;
    }

    // Simulated annealing cool-down
    this.alpha *= 0.9985;

    return totalEnergy;
  }

  /** Reset annealing temperature and randomize velocities */
  reheat(): void {
    this.alpha = 1.0;
    for (const nd of this.nodes) {
      if (nd.pinned) continue;
      nd.vx = (Math.random() - 0.5) * 8;
      nd.vy = (Math.random() - 0.5) * 8;
    }
  }

  /** Find node at world coordinates (with hit radius padding) */
  nodeAt(wx: number, wy: number, zoom: number): SimNode | null {
    let best: SimNode | null = null;
    let bd = Infinity;
    for (const nd of this.nodes) {
      const dx = nd.x - wx;
      const dy = nd.y - wy;
      const d = Math.sqrt(dx * dx + dy * dy);
      const r = nodeRadius(nd.degree) + 6 / zoom;
      if (d < r && d < bd) {
        best = nd;
        bd = d;
      }
    }
    return best;
  }

  /** Compute bounding box of all nodes */
  bounds(): { minX: number; maxX: number; minY: number; maxY: number } {
    if (this.nodes.length === 0) {
      return { minX: -100, maxX: 100, minY: -100, maxY: 100 };
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const nd of this.nodes) {
      if (nd.x < minX) minX = nd.x;
      if (nd.x > maxX) maxX = nd.x;
      if (nd.y < minY) minY = nd.y;
      if (nd.y > maxY) maxY = nd.y;
    }
    return { minX, maxX, minY, maxY };
  }

  // ── Stats ────────────────────────────────────────────

  get nodeCount(): number {
    return this.nodes.length;
  }

  get edgeCount(): number {
    return this.edges.length;
  }

  get density(): number {
    const n = this.nodes.length;
    const maxE = (n * (n - 1)) / 2;
    return maxE > 0 ? this.edges.length / maxE : 0;
  }

  get maxDegree(): number {
    if (this.nodes.length === 0) return 0;
    return Math.max(...this.nodes.map((nd) => nd.degree));
  }
}
