// ─────────────────────────────────────────────────────────────
// ForceEngine — Pure physics simulation for force-directed graphs
//
// Physics model (ported from graph-visualizer.html):
//   Repulsion  →  Coulomb:  F = k_r · q_i·q_j / r²   (pushes apart)
//   Attraction →  Hooke:    F = k_s · (r − L₀)        (pulls along edge)
//   Gravity    →  Linear:   F = k_g · dist_center      (prevents drift)
//   Integration→  Euler + velocity damping + simulated annealing
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
    this.alpha = 1.0;
  }

  addNode(node: SimNode): void {
    this.idxMap.set(node.id, this.nodes.length);
    this.nodes.push(node);
    if (!this.adj.has(node.id)) {
      this.adj.set(node.id, new Set());
    }
  }

  addEdge(edge: SimEdge): void {
    // Deduplicate
    const key = [edge.source, edge.target].sort().join("\0");
    for (const existing of this.edges) {
      const ek = [existing.source, existing.target].sort().join("\0");
      if (ek === key) return;
    }
    this.edges.push(edge);

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

  // ── Simulation ────────────────────────────────────────

  /** One physics tick. Returns total kinetic energy. */
  tick(): number {
    if (!this.running || this.alpha < 8e-4) return 0;

    const { nodes, edges } = this;
    const cfg = this.config;
    const n = nodes.length;

    // Reset acceleration
    for (const nd of nodes) {
      nd.ax = 0;
      nd.ay = 0;
    }

    // ── 1. Repulsion (Coulomb's law) ──
    // F_rep = k_r · (r_i · r_j) / d²
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = nodes[i]!;
        const b = nodes[j]!;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d = Math.sqrt(dx * dx + dy * dy);
        if (d < 1) {
          dx = Math.random() * 2 - 1;
          dy = Math.random() * 2 - 1;
          d = 1;
        }

        const qi = nodeRadius(a.degree);
        const qj = nodeRadius(b.degree);
        const F = cfg.repulsion * qi * qj / (d * d);
        const fx = (dx / d) * F;
        const fy = (dy / d) * F;

        const mi = nodeMass(cfg, a.degree);
        const mj = nodeMass(cfg, b.degree);
        a.ax -= fx / mi;
        a.ay -= fy / mi;
        b.ax += fx / mj;
        b.ay += fy / mj;
      }
    }

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
