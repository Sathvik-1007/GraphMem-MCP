"""Local embedding engine with lazy loading, ONNX optimization, and caching.

Priorities:
1. ONNX Runtime (if installed and use_onnx=True) — 3x faster, lower RAM
2. PyTorch via sentence-transformers — fallback
3. Graceful degradation — if neither works, semantic search is disabled

The model is loaded lazily on first use, not at import time. Embeddings are
cached in the database keyed by ``(content hash, model name)`` — both halves,
so two models can share one cache instead of overwriting each other.

The cache is LRU: every hit refreshes the row's ``created_at``, and eviction
drops the oldest such timestamps, so ``cache_size`` bounds the entries you
actually still use.  (The column keeps its original name because the storage
backend's eviction query orders by it; it holds "last used", not "created".)

The engine is **storage-agnostic**: it delegates all persistence to a
:class:`SQLiteBackend` instance.

Error contract
--------------
- ``initialize()`` sets ``available = True`` optimistically and never raises.
- ``_ensure_model_loaded()`` is called lazily on first ``embed()`` call.
  If loading fails it sets ``available = False`` and raises ``EmbeddingError``.
- ``embed()`` raises ``EmbeddingError`` when the engine is unavailable.
- Callers in ``server.py`` (``_embed_entities``, ``_embed_observations``) check
  ``available`` and silently skip embedding when ``False``.
- Callers in ``search.py`` (``_vector_search``) catch ``EmbeddingError`` and
  degrade to FTS-only search, returning an empty vector result set.

These three strategies (skip, raise, catch-and-degrade) are intentional layers:
server helpers are fire-and-forget, the engine is strict, and search is resilient.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sqlite3
import threading
import time
from functools import partial
from typing import TYPE_CHECKING, Protocol

import numpy as np
import numpy.typing as npt

from graph_mem.utils.errors import DimensionMismatchError, EmbeddingError, ModelLoadError
from graph_mem.utils.logging import get_logger

if TYPE_CHECKING:
    from graph_mem.storage import SQLiteBackend


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

# SQLite refuses a statement with more than SQLITE_MAX_VARIABLE_NUMBER bound
# parameters — 999 on builds older than 3.32.  900 leaves room for the extra
# non-id parameters each batched statement binds and is valid on every build.
_MAX_SQL_VARIABLES = 900

# Rows per batched cache INSERT: each row binds 4 parameters.
_CACHE_ROWS_PER_INSERT = _MAX_SQL_VARIABLES // 4

# Texts per model.encode() call.  Bigger batches stop helping once the model
# saturates the CPU/GPU and only grow peak memory; 64 is sentence-transformers'
# own default working set.
_ENCODE_BATCH_SIZE = 64

# Batches between cache prunes.  Pruning costs a COUNT(*) over the whole cache,
# so doing it after every batch makes each write O(cache size).  Checking every
# 32nd batch amortises that away and lets the cache overshoot ``cache_size`` by
# at most 32 batches' worth of entries before it is trimmed back.
_PRUNE_EVERY_N_BATCHES = 32


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _onnx_backend_available() -> bool:
    """Whether ``SentenceTransformer(backend="onnx")`` has its dependencies.

    That backend runs the model through ``optimum.onnxruntime`` (which depends
    on onnxruntime itself), so probing for a bare ``onnxruntime`` install says
    nothing about whether it will work.  ``optimum`` is the thing to look for.

    It also needs sentence-transformers >= 3.2 for the ``backend`` keyword;
    that is not probed here — an older release raises ``TypeError`` from the
    constructor, which the caller already treats as "fall back to PyTorch".
    """
    try:
        return importlib.util.find_spec("optimum.onnxruntime") is not None
    except (ImportError, ValueError):
        # ImportError: ``optimum`` itself is missing. ValueError: it is
        # installed but broken (no __spec__).  Either way, no ONNX backend.
        return False


def _embedding_to_bytes(embedding: list[float] | np.ndarray) -> bytes:
    arr = np.asarray(embedding, dtype=np.float32)
    return arr.tobytes()


def _bytes_to_embedding(data: bytes) -> list[float]:
    values: list[float] = np.frombuffer(data, dtype=np.float32).tolist()
    return values


class EmbeddingEngine:
    """Local embedding engine with lazy model loading and caching.

    Usage::
        engine = EmbeddingEngine(model_name="all-MiniLM-L6-v2", use_onnx=True, device="cpu")
        await engine.initialize(storage)  # stores config only — fast, no model load
        vectors = await engine.embed(["hello world", "test"])  # model loads here on first call
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
        self._storage: SQLiteBackend | None = None
        self._model_loaded = False  # True once _ensure_model_loaded() succeeds
        self._stored_dimension: int | None = None  # Cached from DB metadata
        self._stored_model_name: str | None = None  # Model that wrote the stored vectors
        self._batches_since_prune = 0  # Amortises prune_embedding_cache()
        self._load_lock = threading.Lock()  # Guards lazy model loading (thread safety for pre-warm)

    def set_storage(self, storage: SQLiteBackend) -> None:
        """Store a storage backend reference for use in subsequent calls."""
        self._storage = storage

    def _resolve_storage(self, storage: SQLiteBackend | None = None) -> SQLiteBackend:
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

    @property
    def stored_model_name(self) -> str | None:
        """Model that wrote the vectors in this database, if it is recorded."""
        return self._stored_model_name

    @property
    def vectors_stale(self) -> bool:
        """Whether the stored vectors came from a different model.

        Two models of the same dimensionality still embed into different
        spaces, so ranking new queries against old vectors is noise rather
        than an error — the dimension check cannot catch it.  Callers should
        report this and re-embed; nothing is deleted here.
        """
        return self._stored_model_name is not None and self._stored_model_name != self._model_name

    def _load_model(self) -> None:
        """Load the embedding model, trying ONNX first then PyTorch fallback."""
        if self._model is not None:
            return

        # Try ONNX first
        if self._use_onnx and _onnx_backend_available():
            try:
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
                "sentence-transformers not installed. Install with: pip install graph-mem"
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

    def _ensure_model_loaded(self) -> None:
        """Lazy-load the model on first use. Idempotent after first success.

        This is the core of the lazy loading strategy: ``initialize()`` runs
        fast (no model loading), and the heavyweight PyTorch/ONNX import +
        model download happens here on the first ``embed()`` call.

        Thread-safe: a lock ensures only one thread loads the model, even
        when a background pre-warm thread races with the first ``embed()``
        call from the async event loop.

        If loading fails, ``_available`` is set to ``False`` and an
        ``EmbeddingError`` is raised so callers degrade gracefully.
        """
        if self._model_loaded:
            return

        with self._load_lock:
            # Double-check after acquiring the lock (another thread may have loaded it).
            if self._model_loaded:
                return

            try:
                self._load_model()
                detected_dim = self._detect_dimension()

                # Validate against stored dimension from DB (set during initialize())
                if self._stored_dimension is not None and self._stored_dimension != detected_dim:
                    raise DimensionMismatchError(self._stored_dimension, detected_dim)

                self._dimension = detected_dim
                self._model_loaded = True
                log.info("Lazy model load complete (dim=%d)", detected_dim)
            except (ModelLoadError, DimensionMismatchError):
                self._available = False
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                self._available = False
                raise EmbeddingError(f"Lazy model load failed: {exc}") from exc

    async def initialize(self, storage: SQLiteBackend | None = None) -> None:
        """Prepare the embedding engine for use — fast, no model loading.

        Stores the storage reference, reads the previously-stored dimension
        and model name from DB metadata, and ensures vector tables exist.  The
        actual model load is deferred to the first ``embed()`` call via
        ``_ensure_model_loaded()``, keeping MCP server startup fast.

        If the storage already knows the embedding dimension (from a prior
        session), vector tables are created with that dimension immediately.
        Otherwise table creation is also deferred to first use.

        A model swap is detected here and logged as a warning — see
        :attr:`vectors_stale`.  Stored vectors are left alone.
        """
        storage = self._resolve_storage(storage)
        self._storage = storage
        try:
            # Read stored dimension from a previous session (if any)
            stored_dim_str = await storage.get_metadata("embedding_dimension")
            if stored_dim_str:
                self._stored_dimension = int(stored_dim_str)
                self._dimension = self._stored_dimension

            # Read the model that actually wrote those vectors.  Matching
            # dimensions do not imply matching embedding spaces.
            self._stored_model_name = await storage.get_metadata("embedding_model")
            if self.vectors_stale:
                log.warning(
                    "Embedding model changed: stored vectors were written by %r but this "
                    "engine is configured for %r. The stored vectors are stale — semantic "
                    "ranking against them is meaningless until entities and observations "
                    "are re-embedded. Nothing has been deleted.",
                    self._stored_model_name,
                    self._model_name,
                )

            # Ensure vec tables exist — use stored dimension from this DB,
            # or the dimension already known from a prior graph/model load.
            dim = self._stored_dimension or self._dimension
            if dim is not None:
                await storage.ensure_vec_tables(dim)

            # Mark as available optimistically — model will load lazily.
            # If model load fails later, _ensure_model_loaded() sets
            # _available = False and raises.
            self._available = True
        except (sqlite3.Error, ValueError) as exc:
            log.warning("Embedding initialization failed: %s. Semantic search disabled.", exc)
            self._available = False

    async def _write(self, storage: SQLiteBackend, sql: str, params: tuple[object, ...]) -> None:
        """Run one batched cache write through the storage SQL escape hatch.

        ``SQLiteBackend`` only offers single-row cache writes
        (``set_cached_embedding``), which costs one round trip per vector —
        400 of them around a single batched ``encode()`` for a 200-item batch.
        ``fetch_all`` is the interface's arbitrary-SQL door; the statement
        executes and returns no rows, and backends run in autocommit, so the
        write lands.  Adding a batch-write method to the backend would be the
        cleaner home for this.
        """
        await storage.fetch_all(sql, params)

    async def _read_cache(self, storage: SQLiteBackend, hashes: list[str]) -> dict[str, bytes]:
        """Look up many cached embeddings — one query per chunk, not per text."""
        found: dict[str, bytes] = {}
        step = _MAX_SQL_VARIABLES - 1  # one variable is taken by model_name
        for start in range(0, len(hashes), step):
            chunk = hashes[start : start + step]
            placeholders = ",".join("?" for _ in chunk)
            rows = await storage.fetch_all(
                "SELECT content_hash, embedding FROM embedding_cache "
                f"WHERE model_name = ? AND content_hash IN ({placeholders})",
                (self._model_name, *chunk),
            )
            found.update({str(r["content_hash"]): bytes(r["embedding"]) for r in rows})
        return found

    async def _touch_cache(self, storage: SQLiteBackend, hashes: list[str], now: float) -> None:
        """Mark cache hits as used now, which is what makes eviction LRU."""
        step = _MAX_SQL_VARIABLES - 2  # two variables taken by now + model_name
        for start in range(0, len(hashes), step):
            chunk = hashes[start : start + step]
            placeholders = ",".join("?" for _ in chunk)
            await self._write(
                storage,
                "UPDATE embedding_cache SET created_at = ? "
                f"WHERE model_name = ? AND content_hash IN ({placeholders})",
                (now, self._model_name, *chunk),
            )

    async def _write_cache(
        self, storage: SQLiteBackend, rows: list[tuple[str, bytes, str, float]]
    ) -> None:
        """Store many freshly computed embeddings in one statement per chunk."""
        for start in range(0, len(rows), _CACHE_ROWS_PER_INSERT):
            chunk = rows[start : start + _CACHE_ROWS_PER_INSERT]
            values = ",".join("(?, ?, ?, ?)" for _ in chunk)
            params: list[object] = [field for row in chunk for field in row]
            await self._write(
                storage,
                "INSERT OR REPLACE INTO embedding_cache "
                f"(content_hash, embedding, model_name, created_at) VALUES {values}",
                tuple(params),
            )

    async def _maybe_prune(self, storage: SQLiteBackend) -> None:
        """Prune the cache every :data:`_PRUNE_EVERY_N_BATCHES` batches."""
        self._batches_since_prune += 1
        if self._batches_since_prune < _PRUNE_EVERY_N_BATCHES:
            return
        self._batches_since_prune = 0
        await storage.prune_embedding_cache(self._cache_size)

    async def embed(
        self, texts: list[str], storage: SQLiteBackend | None = None
    ) -> list[list[float] | None]:
        """Compute embeddings with caching.

        Cache lookups, cache writes and the model call are each batched: one
        query for the whole batch's cache hits, one ``encode()``, one insert
        per chunk of results.  ``encode()`` runs in a worker thread, since it
        is CPU-bound for hundreds of milliseconds and would otherwise block
        every other request on the event loop.

        Args:
            texts: Strings to embed.
            storage: Storage backend for cache access. Falls back to ``self._storage``.

        Returns:
            List of embedding vectors, same order and length as *texts*.
            The ``None`` in the return type is for callers that must handle a
            partially-failed batch; this implementation fills every slot or
            raises.
        """
        if not self._available:
            raise EmbeddingError("Embedding engine not available.")

        # Lazy-load model on first embed() call.
        # Run in thread to avoid blocking the async event loop if prewarm failed.
        await asyncio.to_thread(self._ensure_model_loaded)

        storage = self._resolve_storage(storage)

        # If this is the first session (no stored dimension), persist metadata now
        if self._stored_dimension is None and self._dimension is not None:
            await storage.set_metadata("embedding_dimension", str(self._dimension))
            await storage.set_metadata("embedding_model", self._model_name)
            await storage.ensure_vec_tables(self._dimension)
            self._stored_dimension = self._dimension
            self._stored_model_name = self._model_name

        now = time.time()
        hashes = [_content_hash(t) for t in texts]
        cache = await self._read_cache(storage, list(dict.fromkeys(hashes)))

        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []
        for i, (text, h) in enumerate(zip(texts, hashes, strict=True)):
            blob = cache.get(h)
            if blob is not None:
                results[i] = _bytes_to_embedding(blob)
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if cache:
            await self._touch_cache(storage, list(cache), now)

        # Compute uncached
        if uncached_texts:
            model = self._model
            if model is None:
                raise EmbeddingError(
                    "Model failed to load — _ensure_model_loaded did not set _model."
                )
            # Off the event loop: encode() is 50-500 ms+ of CPU per batch.
            vectors = await asyncio.to_thread(
                partial(
                    model.encode,
                    uncached_texts,
                    normalize_embeddings=True,
                    batch_size=min(_ENCODE_BATCH_SIZE, len(uncached_texts)),
                    show_progress_bar=False,
                )
            )

            # The one post-condition that can actually fail: a model returning
            # a different number of vectors than it was given texts would
            # otherwise leave silent None holes in the result.
            if len(vectors) != len(uncached_texts):
                raise EmbeddingError(
                    f"Model returned {len(vectors)} vectors for {len(uncached_texts)} texts."
                )

            new_rows: list[tuple[str, bytes, str, float]] = []
            for text_index, vec in zip(uncached_indices, vectors, strict=True):
                results[text_index] = vec.tolist()
                new_rows.append(
                    (hashes[text_index], _embedding_to_bytes(vec), self._model_name, now)
                )

            await self._write_cache(storage, new_rows)
            await self._maybe_prune(storage)

        return results

    async def upsert_entity_embedding(
        self, entity_id: str, embedding: list[float], storage: SQLiteBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        blob = _embedding_to_bytes(embedding)
        await storage.upsert_entity_embedding(entity_id, blob)

    async def upsert_observation_embedding(
        self, obs_id: str, embedding: list[float], storage: SQLiteBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        blob = _embedding_to_bytes(embedding)
        await storage.upsert_observation_embedding(obs_id, blob)

    async def delete_entity_embedding(
        self, entity_id: str, storage: SQLiteBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        await storage.delete_entity_embedding(entity_id)

    async def delete_observation_embedding(
        self, obs_id: str, storage: SQLiteBackend | None = None
    ) -> None:
        storage = self._resolve_storage(storage)
        await storage.delete_observation_embedding(obs_id)
