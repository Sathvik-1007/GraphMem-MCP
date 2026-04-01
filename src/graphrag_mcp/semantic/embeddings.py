"""Local embedding engine with lazy loading, ONNX optimization, and caching.

Priorities:
1. ONNX Runtime (if installed and use_onnx=True) — 3x faster, lower RAM
2. PyTorch via sentence-transformers — fallback
3. Graceful degradation — if neither works, semantic search is disabled

The model is loaded lazily on first use, not at import time. Embeddings
are cached by content hash (SHA-256) in the database to avoid recomputation.

The engine is **storage-agnostic**: it delegates all persistence to a
:class:`StorageBackend` instance.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import time
from typing import TYPE_CHECKING, Protocol

import numpy as np
import numpy.typing as npt

from graphrag_mcp.utils.errors import DimensionMismatchError, EmbeddingError, ModelLoadError
from graphrag_mcp.utils.logging import get_logger

if TYPE_CHECKING:
    from graphrag_mcp.storage.base import StorageBackend


class EmbeddingModel(Protocol):
    """Structural type for sentence-transformer-like embedding models."""

    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool = ...,
        batch_size: int = ...,
        show_progress_bar: bool = ...,
    ) -> npt.NDArray[np.float32]: ...


log = get_logger("semantic.embeddings")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embedding_to_bytes(embedding: list[float] | np.ndarray) -> bytes:
    arr = np.asarray(embedding, dtype=np.float32)
    return arr.tobytes()


def _bytes_to_embedding(data: bytes) -> list[float]:
    return list(struct.unpack(f"{len(data) // 4}f", data))


class EmbeddingEngine:
    """Local embedding engine with lazy model loading and caching.

    Usage::
        engine = EmbeddingEngine(model_name="all-MiniLM-L6-v2", use_onnx=True, device="cpu")
        await engine.initialize(storage)  # loads model, checks dimension
        vectors = await engine.embed(["hello world", "test"])
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
        self._model: EmbeddingModel | None = None
        self._dimension: int | None = None
        self._available = False
        self._storage: StorageBackend | None = None

    def set_storage(self, storage: StorageBackend) -> None:
        """Store a storage backend reference for use in subsequent calls."""
        self._storage = storage

    def _resolve_storage(self, storage: StorageBackend | None = None) -> StorageBackend:
        """Return the explicitly passed storage, or fall back to self._storage."""
        if storage is not None:
            return storage
        if self._storage is not None:
            return self._storage
        raise EmbeddingError(
            "No storage available. Pass storage explicitly "
            "or call set_storage()/initialize() first."
        )

    @property
    def dimension(self) -> int:
        """Embedding dimensionality. Raises if model not loaded."""
        if self._dimension is None:
            raise EmbeddingError("Embedding model not initialized.")
        return self._dimension

    @property
    def available(self) -> bool:
        return self._available

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load_model(self) -> None:
        """Load the embedding model, trying ONNX first then PyTorch fallback."""
        if self._model is not None:
            return

        # Try ONNX first
        if self._use_onnx:
            try:
                import onnxruntime  # type: ignore[import-not-found]  # noqa: F401
                from sentence_transformers import (  # type: ignore[import-not-found]
                    SentenceTransformer,
                )

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
            except (ImportError, OSError, RuntimeError) as e:
                log.info("ONNX loading failed (%s) — falling back to PyTorch", e)

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
        except (OSError, RuntimeError) as exc:
            raise ModelLoadError(
                f"Failed to load embedding model {self._model_name!r}: {exc}"
            ) from exc

    def _detect_dimension(self) -> int:
        """Run a test embedding to detect output dimensionality."""
        self._load_model()
        if self._model is None:
            raise EmbeddingError("Model failed to load — _load_model did not set _model.")
        test = self._model.encode(["test"], normalize_embeddings=True)
        dim = int(test.shape[1])
        log.info("Detected embedding dimension: %d", dim)
        return dim

    async def initialize(self, storage: StorageBackend | None = None) -> None:
        """Load model, detect dimension, validate against stored metadata.

        If the storage has no dimension stored, stores the detected one.
        If it has a different dimension, raises DimensionMismatchError.
        """
        storage = self._resolve_storage(storage)
        try:
            self._load_model()
            self._dimension = self._detect_dimension()

            # Check stored dimension
            stored_dim_str = await storage.get_metadata("embedding_dimension")
            if stored_dim_str:
                stored_dim = int(stored_dim_str)
                if stored_dim != self._dimension:
                    raise DimensionMismatchError(stored_dim, self._dimension)
            else:
                await storage.set_metadata("embedding_dimension", str(self._dimension))
                await storage.set_metadata("embedding_model", self._model_name)

            # Create vector tables if they don't exist
            await storage.ensure_vec_tables(self._dimension)

            self._available = True
            self._storage = storage
        except (ModelLoadError, DimensionMismatchError):
            raise
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            log.warning("Embedding initialization failed: %s. Semantic search disabled.", exc)
            self._available = False

    async def embed(
        self, texts: list[str], storage: StorageBackend | None = None
    ) -> list[list[float] | None]:
        """Compute embeddings with caching.

        Checks the embedding_cache table first. Only computes embeddings
        for texts not already cached.

        Args:
            texts: Strings to embed.
            storage: Storage backend for cache access. Falls back to ``self._storage``.

        Returns:
            List of embedding vectors (same order as texts).
            A ``None`` entry means embedding failed or was unavailable for that text.
        """
        if not self._available:
            raise EmbeddingError("Embedding engine not available.")

        storage = self._resolve_storage(storage)

        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # Check cache
        for i, text in enumerate(texts):
            h = _content_hash(text)
            cached = await storage.get_cached_embedding(h, self._model_name)
            if cached:
                results[i] = _bytes_to_embedding(cached)
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Compute uncached
        if uncached_texts:
            self._load_model()
            if self._model is None:
                raise EmbeddingError("Model failed to load — _load_model did not set _model.")
            vectors = self._model.encode(
                uncached_texts,
                normalize_embeddings=True,
                batch_size=min(64, len(uncached_texts)),
                show_progress_bar=False,
            )

            now = time.time()
            for idx, (text, vec) in enumerate(zip(uncached_texts, vectors, strict=True)):
                vec_list = vec.tolist()
                results[uncached_indices[idx]] = vec_list

                # Cache
                h = _content_hash(text)
                blob = _embedding_to_bytes(vec)
                await storage.set_cached_embedding(h, blob, self._model_name, now)

            # Prune cache if over limit
            await storage.prune_embedding_cache(self._cache_size)

        # Post-condition: all entries must be non-None after successful computation
        for i, entry in enumerate(results):
            if entry is None:
                raise EmbeddingError(f"Embedding computation produced None for text at index {i}.")

        return results

    async def upsert_entity_embedding(
        self, entity_id: str, embedding: list[float], storage: StorageBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        blob = _embedding_to_bytes(embedding)
        await storage.upsert_entity_embedding(entity_id, blob)

    async def upsert_observation_embedding(
        self, obs_id: str, embedding: list[float], storage: StorageBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        blob = _embedding_to_bytes(embedding)
        await storage.upsert_observation_embedding(obs_id, blob)

    async def delete_entity_embedding(
        self, entity_id: str, storage: StorageBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        await storage.delete_entity_embedding(entity_id)

    async def delete_observation_embedding(
        self, obs_id: str, storage: StorageBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        await storage.delete_observation_embedding(obs_id)
