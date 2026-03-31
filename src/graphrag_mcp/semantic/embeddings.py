"""Local embedding engine with lazy loading, ONNX optimization, and caching.

Priorities:
1. ONNX Runtime (if installed and use_onnx=True) — 3x faster, lower RAM
2. PyTorch via sentence-transformers — fallback
3. Graceful degradation — if neither works, semantic search is disabled

The model is loaded lazily on first use, not at import time. Embeddings
are cached by content hash (SHA-256) in the database to avoid recomputation.
"""

from __future__ import annotations

import hashlib
import struct
import time
from typing import TYPE_CHECKING

import numpy as np

from graphrag_mcp.utils.errors import DimensionMismatchError, EmbeddingError, ModelLoadError
from graphrag_mcp.utils.logging import get_logger

if TYPE_CHECKING:
    from graphrag_mcp.db.connection import Database

log = get_logger("semantic.embeddings")


def _content_hash(text: str) -> str:
    """SHA-256 hash of text for cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embedding_to_bytes(embedding: list[float] | np.ndarray) -> bytes:
    """Pack a float array into raw bytes for SQLite BLOB storage."""
    arr = np.asarray(embedding, dtype=np.float32)
    return arr.tobytes()


def _bytes_to_embedding(data: bytes) -> list[float]:
    """Unpack raw bytes into a float list."""
    return list(struct.unpack(f"{len(data) // 4}f", data))


class EmbeddingEngine:
    """Local embedding engine with lazy model loading and caching.

    Usage::
        engine = EmbeddingEngine(model_name="all-MiniLM-L6-v2", use_onnx=True, device="cpu")
        await engine.initialize(db)  # loads model, checks dimension
        vectors = await engine.embed(["hello world", "test"], db)
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        use_onnx: bool = True,
        device: str = "cpu",
        cache_size: int = 10000,
    ) -> None:
        self._model_name = model_name
        self._use_onnx = use_onnx
        self._device = device
        self._cache_size = cache_size
        self._model: object | None = None
        self._dimension: int | None = None
        self._available = False

    @property
    def dimension(self) -> int:
        """Embedding dimensionality. Raises if model not loaded."""
        if self._dimension is None:
            raise EmbeddingError("Embedding model not initialized.")
        return self._dimension

    @property
    def available(self) -> bool:
        """Whether the embedding engine is operational."""
        return self._available

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load_model(self) -> None:
        """Load the embedding model (ONNX or PyTorch)."""
        if self._model is not None:
            return

        # Try ONNX first
        if self._use_onnx:
            try:
                import onnxruntime  # noqa: F401
                from sentence_transformers import SentenceTransformer

                log.info("Loading model %s with ONNX backend", self._model_name)
                # Try to use ONNX-optimized model
                self._model = SentenceTransformer(
                    self._model_name,
                    device=self._device,
                    backend="onnx",
                )
                self._available = True
                log.info("ONNX model loaded successfully")
                return
            except Exception as e:
                log.debug("ONNX loading failed (%s), falling back to PyTorch", e)

        # Fall back to PyTorch
        try:
            from sentence_transformers import SentenceTransformer

            log.info("Loading model %s with PyTorch backend", self._model_name)
            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,
            )
            self._available = True
            log.info("PyTorch model loaded successfully")
        except ImportError as exc:
            raise ModelLoadError(
                "sentence-transformers not installed. Install with: pip install graphrag-mcp"
            ) from exc
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load embedding model {self._model_name!r}: {exc}"
            ) from exc

    def _detect_dimension(self) -> int:
        """Run a test embedding to detect output dimensionality."""
        self._load_model()
        test = self._model.encode(["test"], normalize_embeddings=True)
        dim = int(test.shape[1])
        log.info("Detected embedding dimension: %d", dim)
        return dim

    async def initialize(self, db: Database) -> None:
        """Load model, detect dimension, validate against DB metadata.

        If the database has no dimension stored, stores the detected one.
        If it has a different dimension, raises DimensionMismatchError.
        """
        try:
            self._load_model()
            self._dimension = self._detect_dimension()

            # Check stored dimension
            row = await db.fetch_one("SELECT value FROM metadata WHERE key = 'embedding_dimension'")
            if row:
                stored_dim = int(row["value"])
                if stored_dim != self._dimension:
                    raise DimensionMismatchError(stored_dim, self._dimension)
            else:
                await db.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                    ("embedding_dimension", str(self._dimension)),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                    ("embedding_model", self._model_name),
                )

            # Create vector tables if they don't exist
            await self._ensure_vec_tables(db)

            self._available = True
        except (ModelLoadError, DimensionMismatchError):
            raise
        except Exception as exc:
            log.warning("Embedding initialization failed: %s. Semantic search disabled.", exc)
            self._available = False

    async def _ensure_vec_tables(self, db: Database) -> None:
        """Create sqlite-vec virtual tables if they don't exist."""
        dim = self._dimension
        try:
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS entity_embeddings USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{dim}]
                )
            """)
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS observation_embeddings USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{dim}]
                )
            """)
        except Exception as exc:
            log.warning("Could not create vector tables: %s", exc)
            self._available = False

    async def embed(self, texts: list[str], db: Database) -> list[list[float]]:
        """Compute embeddings with caching.

        Checks the embedding_cache table first. Only computes embeddings
        for texts not already cached.

        Args:
            texts: Strings to embed.
            db: Database for cache access.

        Returns:
            List of embedding vectors (same order as texts).
        """
        if not self._available:
            raise EmbeddingError("Embedding engine not available.")

        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # Check cache
        for i, text in enumerate(texts):
            h = _content_hash(text)
            row = await db.fetch_one(
                "SELECT embedding FROM embedding_cache WHERE content_hash = ? AND model_name = ?",
                (h, self._model_name),
            )
            if row:
                results[i] = _bytes_to_embedding(row["embedding"])
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Compute uncached
        if uncached_texts:
            self._load_model()
            vectors = self._model.encode(
                uncached_texts,
                normalize_embeddings=True,
                batch_size=min(64, len(uncached_texts)),
                show_progress_bar=False,
            )

            now = time.time()
            for idx, (text, vec) in enumerate(zip(uncached_texts, vectors)):
                vec_list = vec.tolist()
                results[uncached_indices[idx]] = vec_list

                # Cache
                h = _content_hash(text)
                blob = _embedding_to_bytes(vec)
                await db.execute(
                    "INSERT OR REPLACE INTO embedding_cache (content_hash, embedding, model_name, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (h, blob, self._model_name, now),
                )

            # Prune cache if over limit
            count_row = await db.fetch_one("SELECT COUNT(*) AS c FROM embedding_cache")
            if count_row and int(count_row["c"]) > self._cache_size:
                excess = int(count_row["c"]) - self._cache_size
                await db.execute(
                    "DELETE FROM embedding_cache WHERE content_hash IN "
                    "(SELECT content_hash FROM embedding_cache ORDER BY created_at ASC LIMIT ?)",
                    (excess,),
                )

        return results  # type: ignore[return-value]

    async def upsert_entity_embedding(
        self, entity_id: str, embedding: list[float], db: Database
    ) -> None:
        """Store or update an entity's embedding in the vector table."""
        blob = _embedding_to_bytes(embedding)
        await db.execute(
            "INSERT OR REPLACE INTO entity_embeddings (id, embedding) VALUES (?, ?)",
            (entity_id, blob),
        )

    async def upsert_observation_embedding(
        self, obs_id: str, embedding: list[float], db: Database
    ) -> None:
        """Store or update an observation's embedding in the vector table."""
        blob = _embedding_to_bytes(embedding)
        await db.execute(
            "INSERT OR REPLACE INTO observation_embeddings (id, embedding) VALUES (?, ?)",
            (obs_id, blob),
        )

    async def delete_entity_embedding(self, entity_id: str, db: Database) -> None:
        """Remove an entity's embedding."""
        await db.execute("DELETE FROM entity_embeddings WHERE id = ?", (entity_id,))

    async def delete_observation_embedding(self, obs_id: str, db: Database) -> None:
        """Remove an observation's embedding."""
        await db.execute("DELETE FROM observation_embeddings WHERE id = ?", (obs_id,))
