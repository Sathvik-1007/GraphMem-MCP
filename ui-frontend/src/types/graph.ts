// ── API Response Types ──

export interface GraphEntity {
  name: string;
  entity_type: string;
  description: string;
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  source: string;
  target: string;
  relationship_type: string;
  weight: number;
  properties: Record<string, unknown>;
}

export interface GraphResponse {
  entities: GraphEntity[];
  relationships: GraphRelationship[];
  total_entities: number;
  total_relationships: number;
}

export interface EntityObservation {
  id: string;
  content: string;
  source: string;
  created_at: string | number | null;
}

export interface EntityRelationship {
  source: string;
  target: string;
  relationship_type: string;
  weight: number;
  direction: "outgoing" | "incoming";
  properties: Record<string, unknown>;
}

export interface EntityResponse {
  name: string;
  entity_type: string;
  description: string;
  properties: Record<string, unknown>;
  observations: EntityObservation[];
  relationships: EntityRelationship[];
}

export interface SearchResult {
  name: string;
  entity_type: string;
  description: string;
  score: number;
  matched_observations: string[];
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total_results: number;
}

export interface ConnectedEntity {
  name: string;
  connection_count: number;
}

export interface RecentEntity {
  name: string;
  updated_at: string | null;
}

export interface StatsResponse {
  entity_count: number;
  relationship_count: number;
  observation_count: number;
  entity_type_distribution: Record<string, number>;
  relationship_type_distribution: Record<string, number>;
  most_connected_entities: ConnectedEntity[];
  recently_updated: RecentEntity[];
}

// ── App State Types ──

export interface AppState {
  graph: GraphResponse | null;
  selectedEntity: EntityResponse | null;
  stats: StatsResponse | null;
  searchResults: SearchResponse | null;
  visibleEntityTypes: Set<string>;
  showEdgeLabels: boolean;
  loading: boolean;
  error: string | null;
}

export type AppAction =
  | { type: "SET_GRAPH"; payload: GraphResponse }
  | { type: "SET_ENTITY"; payload: EntityResponse | null }
  | { type: "SET_STATS"; payload: StatsResponse }
  | { type: "SET_SEARCH"; payload: SearchResponse | null }
  | { type: "TOGGLE_ENTITY_TYPE"; payload: string }
  | { type: "SET_ALL_TYPES"; payload: Set<string> }
  | { type: "TOGGLE_EDGE_LABELS" }
  | { type: "SET_LOADING"; payload: boolean }
  | { type: "SET_ERROR"; payload: string | null };
