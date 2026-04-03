// ── Curated entity type color palette ──
// Vibrant colors optimized for OLED dark backgrounds (high saturation, good contrast)
// Fallback: deterministic hash-based hex for unknown types

const ENTITY_COLORS: Record<string, string> = {
  character: "#f472b6",    // pink-400
  person: "#f472b6",       // pink-400
  decision: "#818cf8",     // indigo-400
  feature: "#34d399",      // emerald-400
  concept: "#fb923c",      // orange-400
  artifact: "#c084fc",     // purple-400
  place: "#22d3ee",        // cyan-400
  location: "#22d3ee",     // cyan-400
  document: "#fbbf24",     // amber-400
  release: "#f87171",      // red-400
  project: "#60a5fa",      // blue-400
  event: "#a3e635",        // lime-400
  organization: "#2dd4bf", // teal-400
  tool: "#e879f9",         // fuchsia-400
  technology: "#38bdf8",   // sky-400
};

function hslToHex(h: number, s: number, l: number): string {
  s /= 100;
  l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    const color = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    return Math.round(255 * color)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** Get a vibrant hex color for an entity type. Curated palette with hash fallback. */
export function entityColor(type: string): string {
  const lower = type.toLowerCase();
  const curated = ENTITY_COLORS[lower];
  if (curated) return curated;

  // Deterministic hash → vibrant hex
  let hash = 0;
  for (let i = 0; i < lower.length; i++) {
    hash = lower.charCodeAt(i) + ((hash << 5) - hash);
    hash = hash & hash;
  }
  const hue = ((hash % 360) + 360) % 360;
  return hslToHex(hue, 75, 55);
}

// ── Glow palette for canvas rendering ──
// Each entity type gets: fill (dark core), stroke (bright ring), glow (radial halo)

export interface ColorPalette {
  fill: string;
  stroke: string;
  glow: string;
}

/** Hex color → rgba glow string at given opacity */
export function hexToGlow(hex: string, opacity: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${opacity})`;
}

/** Hex color → darkened fill variant (for dark mode) */
function hexToDarkFill(hex: string): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgb(${Math.round(r * 0.25)},${Math.round(g * 0.25)},${Math.round(b * 0.25)})`;
}

/** Hex color → light pastel fill (for light mode) — much more saturated than before */
export function hexToLightFill(hex: string): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  // Blend only 10% toward white — keeps colors vivid and solid on light backgrounds
  return `rgb(${Math.round(r + (255 - r) * 0.10)},${Math.round(g + (255 - g) * 0.10)},${Math.round(b + (255 - b) * 0.10)})`;
}

/** Get canvas glow palette for an entity type */
export function entityColorPalette(type: string): ColorPalette {
  const stroke = entityColor(type);
  return {
    fill: hexToDarkFill(stroke),
    stroke,
    glow: hexToGlow(stroke, 0.55),
  };
}
