# How It Works

> Deep technical reference for graph-mem internals. For installation and usage, see the [README](README.md).

---

## Data Model

The knowledge graph has three core primitives. Every primitive supports full CRUD -- create, read, update, and delete:

```mermaid
erDiagram
    ENTITY {
        string id PK "ULID primary key"
        string name UK "unique display name"
        string entity_type "person, service, concept, etc."
        string description "rich text description"
        json properties "arbitrary key-value metadata"
        float created_at "unix timestamp"
        float updated_at "unix timestamp"
    }
    OBSERVATION {
        string id PK "ULID primary key"
        string entity_id FK "parent entity"
        string content "atomic factual statement"
        string source "provenance (session, doc, etc.)"
        float created_at "unix timestamp"
    }
    RELATIONSHIP {
        string id PK "ULID primary key"
        string source_id FK "source entity"
        string target_id FK "target entity"
        string relationship_type "depends_on, uses, etc."
        float weight "0.0 to 1.0 strength"
        json properties "arbitrary edge metadata"
        float created_at "unix timestamp"
        float updated_at "unix timestamp"
    }
    ENTITY_EMBEDDING {
        string id FK "entity_id"
        blob embedding "float32 vector"
    }
    OBSERVATION_EMBEDDING {
        string id FK "observation_id"
        blob embedding "float32 vector"
    }

    ENTITY ||--o{ OBSERVATION : "has observations"
    ENTITY ||--o{ RELATIONSHIP : "source of"
    ENTITY ||--o{ RELATIONSHIP : "target of"
    ENTITY ||--o| ENTITY_EMBEDDING : "vector index"
    OBSERVATION ||--o| OBSERVATION_EMBEDDING : "vector index"
```

- **Entities** are named nodes with a type and description (e.g., `AuthService`, type `service`).
- **Relationships** are typed, directed, weighted edges between entities (e.g., `AuthService --DEPENDS_ON--> Database`).
- **Observations** are factual statements attached to entities (e.g., "Uses bcrypt for password hashing").
- **Embeddings** are sentence-transformer vectors stored alongside entities and observations for semantic search.

---

## CRUD Operations Map

```mermaid
graph LR
    subgraph Entities["Entity CRUD"]
        E_C["add_entities<br/>create + auto-merge"]
        E_R["get_entity<br/>list_entities"]
        E_U["update_entity<br/>description, type, props"]
        E_D["delete_entities<br/>cascade to obs + rels"]
        E_M["merge_entities<br/>consolidate duplicates"]
    end

    subgraph Relationships["Relationship CRUD"]
        R_C["add_relationships<br/>create + dedup"]
        R_R["find_connections<br/>get_subgraph<br/>find_paths"]
        R_U["update_relationship<br/>weight, type, props"]
        R_D["delete_relationships<br/>by source+target+type"]
    end

    subgraph Observations["Observation CRUD"]
        O_C["add_observations<br/>attach to entity"]
        O_R["search_observations<br/>get_entity (includes obs)"]
        O_U["update_observation<br/>edit content in-place"]
        O_D["delete_observations<br/>by ID with validation"]
    end

    style Entities fill:#6366f1,stroke:#4f46e5,color:#fff
    style Relationships fill:#0ea5e9,stroke:#0284c7,color:#fff
    style Observations fill:#10b981,stroke:#059669,color:#fff
```

---

## Tool Request Flow

Every tool call follows the same pattern through the stack:

```mermaid
sequenceDiagram
    participant Agent as LLM Agent
    participant MCP as MCP Protocol
    participant Tool as Tool Function
    participant Engine as GraphEngine
    participant Embed as EmbeddingEngine
    participant Storage as SQLiteBackend
    participant DB as SQLite DB

    Agent->>MCP: tool call (e.g. add_entities)
    MCP->>Tool: dispatch to handler
    Tool->>Engine: validate + build domain objects
    Engine->>Storage: begin transaction
    Storage->>DB: INSERT/UPDATE/DELETE
    DB-->>Storage: result
    Storage-->>Engine: commit
    Engine-->>Tool: domain result
    Tool->>Embed: compute embeddings (async)
    Embed->>Storage: upsert vectors
    Tool-->>MCP: JSON response
    MCP-->>Agent: tool result
```

---

## Hybrid Search Pipeline

`search_nodes` combines three retrieval strategies using Reciprocal Rank Fusion (RRF):

```mermaid
graph TB
    Query["Search Query<br/>'authentication service'"]

    subgraph VectorPath["Vector Search Path"]
        Encode["Encode query<br/>sentence-transformers"]
        VecSearch["sqlite-vec cosine search<br/>top-K nearest neighbors"]
        VecRank["Vector rankings<br/>(entity_id, distance)"]
    end

    subgraph FTSPath["Full-Text Search Path"]
        Tokenize["Tokenize + stem query<br/>FTS5 query syntax"]
        FTSSearch["SQLite FTS5 BM25 search<br/>entities + observations"]
        FTSRank["FTS rankings<br/>(entity_id, bm25_score)"]
    end

    subgraph Fusion["RRF Fusion"]
        Merge["Reciprocal Rank Fusion<br/>score = sum(1 / (k + rank_i))"]
        Dedup["Deduplicate + merge scores"]
        Sort["Sort by fused score"]
    end

    subgraph Enrich["Result Enrichment"]
        FetchEntity["Fetch entity details"]
        FetchRels["Fetch relationships"]
        FetchObs["Fetch observations<br/>(if include_observations=true)"]
    end

    Results["Final scored results<br/>entities with relationships + observations"]

    Query --> VectorPath
    Query --> FTSPath
    VecRank --> Fusion
    FTSRank --> Fusion
    Sort --> Enrich
    Enrich --> Results

    style Query fill:#6366f1,stroke:#4f46e5,color:#fff
    style Fusion fill:#f59e0b,stroke:#d97706,color:#fff
    style Results fill:#10b981,stroke:#059669,color:#fff
```

1. **Vector similarity** -- cosine distance against sentence-transformer embeddings of entity names, descriptions, and observations.
2. **Full-text search** -- SQLite FTS5 with BM25 ranking for keyword matching.
3. **RRF fusion** -- merges and re-ranks results from both strategies into a single scored list.

When no embedding model is installed, search gracefully degrades to FTS-only mode.

---

## Multi-Hop Traversal

`find_connections` walks the graph recursively using SQL CTEs, discovering indirect relationships up to a configurable depth. This surfaces connections that no flat search can find -- like tracing a function through three layers of abstraction to the database schema it ultimately modifies.

```mermaid
graph LR
    A["AuthService"] -->|DEPENDS_ON| B["UserStore"]
    B -->|READS_FROM| C["PostgresDB"]
    C -->|HOSTS| D["users_table"]

    style A fill:#f472b6,stroke:#ec4899,color:#fff
    style D fill:#34d399,stroke:#10b981,color:#fff
```

A query like `find_connections("AuthService", max_hops=3)` traverses the full chain `AuthService -> UserStore -> PostgresDB -> users_table`, even though `users_table` never mentions "auth."

---

## Entity Resolution

When the agent references an entity by name, graph-mem resolves it through a cascade:

```mermaid
flowchart TD
    Input["Agent references entity name<br/>(e.g. 'authservice')"]
    Exact["1. Exact match<br/>name = 'authservice'<br/>+ optional type constraint"]
    CI["2. Case-insensitive match<br/>LOWER(name) = 'authservice'"]
    FTS["3. FTS5 fuzzy search<br/>similar names via full-text index"]
    Found["Entity resolved<br/>return Entity object with ID"]
    Suggest["Entity not found<br/>return top-5 suggestions<br/>so agent can self-correct"]

    Input --> Exact
    Exact -->|"found"| Found
    Exact -->|"miss"| CI
    CI -->|"found"| Found
    CI -->|"miss"| FTS
    FTS -->|"found"| Found
    FTS -->|"miss"| Suggest

    style Found fill:#10b981,stroke:#059669,color:#fff
    style Suggest fill:#f59e0b,stroke:#d97706,color:#fff
    style Input fill:#6366f1,stroke:#4f46e5,color:#fff
```

1. **Exact match** -- case-sensitive name lookup, optionally scoped by entity type.
2. **Case-insensitive match** -- normalized comparison.
3. **FTS5 match** -- full-text search for partial or fuzzy names.
4. **Suggestions** -- if nothing matches, return the closest candidates so the agent can self-correct.

---

## Storage Backend Architecture

```mermaid
graph TB
    subgraph ABC["StorageBackend ABC (50+ abstract methods)"]
        direction LR
        Life["Lifecycle<br/>initialize · close · transaction"]
        EntOps["Entity Ops<br/>upsert · get · list · update · delete · count"]
        RelOps["Relationship Ops<br/>upsert · get · update · delete · count"]
        ObsOps["Observation Ops<br/>insert · get · update · delete · move · count"]
        EmbOps["Embedding Ops<br/>upsert · delete · vector_search · cache"]
        FTSOps["FTS Ops<br/>search_entities · search_observations · suggest"]
        Meta["Metadata + Traversal<br/>get/set metadata · fetch_all · fetch_one"]
    end

    subgraph SQLite["SQLiteBackend (reference implementation)"]
        WAL["WAL mode<br/>concurrent reads"]
        VEC2["sqlite-vec<br/>ANN vectors"]
        FTS2["FTS5 + triggers<br/>auto-sync index"]
        PRAGMA["PRAGMA tuning<br/>journal, cache, mmap"]
    end

    subgraph Future["Future Backends"]
        Neo4j["Neo4j"]
        Memgraph["Memgraph"]
        PG["PostgreSQL"]
    end

    ABC --> SQLite
    ABC -.-> Future

    style ABC fill:#6366f1,stroke:#4f46e5,color:#fff
    style SQLite fill:#0ea5e9,stroke:#0284c7,color:#fff
    style Future fill:#94a3b8,stroke:#64748b,color:#fff
```

---

## Project Structure

```mermaid
graph TD
    Root["src/graph_mem/"]

    Entry["server.py<br/>MCP entry point<br/>19 tool definitions<br/>lifespan + embedding helpers"]
    CLI["cli/<br/>main.py -- Click CLI commands<br/>install.py -- 19-agent skill installer"]
    DB["db/<br/>connection.py -- aiosqlite wrapper<br/>schema.py -- migration runner<br/>migrations/ -- versioned SQL"]
    GraphMod["graph/<br/>engine.py -- CRUD + entity resolution<br/>traversal.py -- BFS + pathfinding + subgraph<br/>merge.py -- entity consolidation"]
    Models["models/<br/>entity.py -- Entity dataclass<br/>relationship.py -- Relationship dataclass<br/>observation.py -- Observation dataclass"]
    SemanticMod["semantic/<br/>embeddings.py -- lazy model + ONNX + cache<br/>search.py -- hybrid vector+FTS5+RRF"]
    StorageMod["storage/<br/>base.py -- StorageBackend ABC (50+ methods)<br/>sqlite_backend.py -- reference impl<br/>__init__.py -- backend registry"]
    UIMod["ui/<br/>server.py -- aiohttp app factory<br/>routes.py -- REST API endpoints<br/>frontend/ -- pre-built React SPA"]
    Utils["utils/<br/>config.py -- Config dataclass + env vars<br/>errors.py -- 14 error classes<br/>ids.py -- ULID generation<br/>logging.py -- structured logging"]

    Root --> Entry
    Root --> CLI
    Root --> DB
    Root --> GraphMod
    Root --> Models
    Root --> SemanticMod
    Root --> StorageMod
    Root --> UIMod
    Root --> Utils

    style Root fill:#6366f1,stroke:#4f46e5,color:#fff
    style Entry fill:#0ea5e9,stroke:#0284c7,color:#fff
```

| Module | Responsibility |
|--------|---------------|
| `server.py` | MCP server entry point, registers all 19 tools, lifespan management, embedding orchestration |
| `cli/` | Click CLI commands (server, init, status, export, import, validate, ui) + skill installer for 19 agents |
| `db/` | Database class (aiosqlite, WAL mode, PRAGMA tuning) + versioned migrations |
| `graph/` | GraphEngine CRUD, BFS traversal, path-finding, subgraph extraction, entity merging |
| `models/` | Dataclasses for Entity, Relationship, Observation |
| `semantic/` | EmbeddingEngine (lazy loading, ONNX, content-hash cache) + HybridSearch (vector + FTS5 + RRF) |
| `storage/` | StorageBackend ABC (50+ methods) + SQLiteBackend reference implementation + backend registry |
| `ui/` | aiohttp web server + REST API routes + pre-built React SPA graph explorer |
| `utils/` | Config, structured logging, error hierarchy (14 classes), ULID generation |
