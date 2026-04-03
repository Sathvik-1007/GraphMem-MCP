import type {
  GraphResponse,
  EntityResponse,
  SearchResponse,
  StatsResponse,
} from "../types/graph";

const BASE = "/api";

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

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
  fields: { description?: string; entity_type?: string; properties?: Record<string, unknown> },
): Promise<{ name: string; entity_type: string; description: string }> {
  return put(`/entity/${encodeURIComponent(name)}`, fields);
}

export function deleteEntity(
  name: string,
): Promise<{ status: string; name: string }> {
  return del(`/entity/${encodeURIComponent(name)}`);
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
