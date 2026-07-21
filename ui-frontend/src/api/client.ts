import type {
  GraphResponse,
  EntityResponse,
  SearchResponse,
  StatsResponse,
} from "../types/graph";

const BASE = "/api";

/** Placeholder left in index.html when the document is not served by the
 *  Python server — i.e. `vite dev`, where the API needs no token. */
const TOKEN_PLACEHOLDER = "__GRAPHMEM_SESSION_TOKEN__";

/**
 * Session token for API calls, read once from the document.
 *
 * The server injects it into index.html at request time and requires it in a
 * custom header on every /api/ request. A custom header is the point: a
 * cross-site request cannot set one without a CORS preflight, and the server
 * rejects preflights from foreign origins.
 */
const SESSION_TOKEN: string = (() => {
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="graphmem-session-token"]',
  );
  const value = meta?.content ?? "";
  return value === TOKEN_PLACEHOLDER ? "" : value;
})();

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (SESSION_TOKEN) {
    headers["X-GraphMem-Token"] = SESSION_TOKEN;
  }
  return headers;
}

/**
 * Single entry point for every API call.
 *
 * Consolidating the four near-identical fetch wrappers this replaced means the
 * auth header cannot be forgotten on a new endpoint, and error handling is
 * defined once.
 */
async function apiFetch<T>(
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const { method = "GET", body } = init;
  const hasBody = body !== undefined;

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: authHeaders(hasBody ? { "Content-Type": "application/json" } : undefined),
    body: hasBody ? JSON.stringify(body) : undefined,
    // The token is carried by the header; no ambient credentials needed.
    credentials: "same-origin",
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    if (res.status === 403) {
      throw new Error(
        "API 403: session token rejected. Reopen the UI using the URL " +
          "printed by `graph-mem ui`.",
      );
    }
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

const request = <T>(path: string) => apiFetch<T>(path);
const post = <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "POST", body });
const put = <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PUT", body });
const del = <T>(path: string) => apiFetch<T>(path, { method: "DELETE" });

export function fetchGraph(
  limit = 200,
  entityTypes?: string[]
): Promise<GraphResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (entityTypes?.length) {
    params.set("entity_types", entityTypes.join(","));
  }
  return request<GraphResponse>(`/graph?${params}`);
}

export function fetchEntity(name: string): Promise<EntityResponse> {
  return request<EntityResponse>(`/entity/${encodeURIComponent(name)}`);
}

export function fetchSearch(
  query: string,
  limit = 10,
  entityTypes?: string[]
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  if (entityTypes?.length) {
    params.set("entity_types", entityTypes.join(","));
  }
  return request<SearchResponse>(`/search?${params}`);
}

export function fetchStats(): Promise<StatsResponse> {
  return request<StatsResponse>("/stats");
}

// ── Write operations ──

export function addEntity(
  name: string,
  entityType: string,
  description: string,
): Promise<{ status: string; entity_name: string }> {
  return post("/entity", { name, entity_type: entityType, description });
}

export function addRelationship(
  source: string,
  target: string,
  relationshipType: string,
  weight = 1.0,
): Promise<{ status: string }> {
  return post("/relationship", {
    source,
    target,
    relationship_type: relationshipType,
    weight,
  });
}

export function addObservations(
  entityName: string,
  observations: string[],
): Promise<{ status: string }> {
  return post("/observations", {
    entity_name: entityName,
    observations,
  });
}

export function updateEntity(
  name: string,
  fields: { name?: string; description?: string; entity_type?: string; properties?: Record<string, unknown> },
): Promise<{ name: string; entity_type: string; description: string }> {
  return put(`/entity/${encodeURIComponent(name)}`, fields);
}

export function deleteEntity(
  name: string,
): Promise<{ status: string; name: string }> {
  return del(`/entity/${encodeURIComponent(name)}`);
}

export function updateObservation(
  obsId: string,
  entityName: string,
  content: string,
): Promise<{ status: string }> {
  return put(`/observation/${encodeURIComponent(obsId)}`, { entity_name: entityName, content });
}

export function deleteObservation(
  obsId: string,
  entityName: string,
): Promise<{ status: string; deleted: number }> {
  return del(`/observation/${encodeURIComponent(obsId)}?entity_name=${encodeURIComponent(entityName)}`);
}

// ── Multi-graph operations ──

export interface GraphInfo {
  name: string;
  file: string;
  entities: number;
  relationships: number;
  observations: number;
  active: boolean;
}

export interface GraphListResponse {
  graphs: GraphInfo[];
  active: string | null;
  graphmem_dir: string;
}

export function fetchGraphs(): Promise<GraphListResponse> {
  return request<GraphListResponse>("/graphs");
}

export function switchGraph(
  name: string,
): Promise<{ status: string; name: string; db_path: string }> {
  return post("/graphs/switch", { name });
}
