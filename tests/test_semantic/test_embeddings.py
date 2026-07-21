"""Tests for EmbeddingEngine uncovered paths — no real model download needed."""

from __future__ import annotations

import logging
import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from graph_mem.db.connection import Database
from graph_mem.db.migrations import v001_initial, v002_embedding_cache_pk
from graph_mem.semantic.embeddings import (
    _PRUNE_EVERY_N_BATCHES,
    EmbeddingEngine,
    _bytes_to_embedding,
    _content_hash,
    _embedding_to_bytes,
    _onnx_backend_available,
)
from graph_mem.storage import SQLiteBackend
from graph_mem.utils.errors import (
    EmbeddingError,
    ModelLoadError,
)

# ---------------------------------------------------------------------------
# Mock storage helper
# ---------------------------------------------------------------------------


def _make_storage(cached: dict[str, bytes] | None = None) -> MagicMock:
    """Return a MagicMock that satisfies StorageBackend async interface.

    ``fetch_all`` emulates the embedding_cache table well enough for the
    engine's batched SELECT/UPDATE/INSERT: cache rows come back for a SELECT,
    writes return no rows.  Every statement is recorded in ``s.statements``.
    """
    rows = cached or {}
    s = MagicMock()
    s.statements = []

    async def fetch_all(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        s.statements.append(sql)
        if sql.lstrip().upper().startswith("SELECT"):
            return [
                {"content_hash": h, "embedding": blob} for h, blob in rows.items() if h in params
            ]
        return []

    s.fetch_all = fetch_all
    s.get_metadata = AsyncMock(return_value=None)
    s.set_metadata = AsyncMock()
    s.ensure_vec_tables = AsyncMock(return_value=True)
    s.get_cached_embedding = AsyncMock(return_value=None)
    s.set_cached_embedding = AsyncMock()
    s.prune_embedding_cache = AsyncMock()
    s.upsert_entity_embedding = AsyncMock()
    s.delete_entity_embedding = AsyncMock()
    s.upsert_observation_embedding = AsyncMock()
    s.delete_observation_embedding = AsyncMock()
    return s


def _statements_starting(storage: MagicMock, keyword: str) -> list[str]:
    return [s for s in storage.statements if s.lstrip().upper().startswith(keyword)]


# ===========================================================================
# 1. Constructor stores params
# ===========================================================================


class TestEmbeddingEngineInit:
    def test_constructor_stores_params(self):
        engine = EmbeddingEngine(
            model_name="custom-model",
            use_onnx=False,
            device="cuda",
            cache_size=500,
        )
        assert engine._model_name == "custom-model"
        assert engine._use_onnx is False
        assert engine._device == "cuda"
        assert engine._cache_size == 500
        assert engine._model is None
        assert engine._dimension is None
        assert engine._available is False
        assert engine._storage is None
        assert engine._model_loaded is False


# ===========================================================================
# 2. available property
# ===========================================================================


class TestAvailableProperty:
    def test_starts_false(self):
        engine = EmbeddingEngine()
        assert engine.available is False

    def test_reflects_internal(self):
        engine = EmbeddingEngine()
        engine._available = True
        assert engine.available is True


# ===========================================================================
# 3. model_name property
# ===========================================================================


class TestModelNameProperty:
    def test_returns_stored_name(self):
        engine = EmbeddingEngine(model_name="my-model")
        assert engine.model_name == "my-model"


# ===========================================================================
# 4. _load_model ONNX + PyTorch both fail
# ===========================================================================


class TestLoadModelFailures:
    def test_pytorch_import_error_raises_model_load_error(self):
        """When sentence_transformers can't be imported, ModelLoadError raised."""
        engine = EmbeddingEngine(model_name="fake", use_onnx=False)
        with (
            patch.dict("sys.modules", {"sentence_transformers": None}),
            pytest.raises(ModelLoadError, match="sentence-transformers not installed"),
        ):
            engine._load_model()
        assert engine._available is False

    def test_pytorch_oserror_raises_model_load_error(self):
        """When PyTorch SentenceTransformer raises OSError, ModelLoadError raised."""
        engine = EmbeddingEngine(model_name="nonexistent", use_onnx=False)
        mock_st = MagicMock()
        mock_st.SentenceTransformer.side_effect = OSError("model not found")

        with (
            patch.dict("sys.modules", {"sentence_transformers": mock_st}),
            pytest.raises(ModelLoadError, match="Failed to load embedding model"),
        ):
            engine._load_model()

    def test_pytorch_runtime_error_raises_model_load_error(self):
        """When PyTorch SentenceTransformer raises RuntimeError, ModelLoadError raised."""
        engine = EmbeddingEngine(model_name="bad", use_onnx=False)
        mock_st = MagicMock()
        mock_st.SentenceTransformer.side_effect = RuntimeError("CUDA fail")

        with (
            patch.dict("sys.modules", {"sentence_transformers": mock_st}),
            pytest.raises(ModelLoadError, match="Failed to load embedding model"),
        ):
            engine._load_model()


# ===========================================================================
# 5. _load_model ONNX succeeds
# ===========================================================================


class TestLoadModelOnnxSuccess:
    def test_onnx_path_succeeds(self):
        engine = EmbeddingEngine(model_name="test", use_onnx=True)
        mock_model = MagicMock()
        mock_st = MagicMock()
        mock_st.SentenceTransformer.return_value = mock_model

        with (
            patch(
                "graph_mem.semantic.embeddings._onnx_backend_available",
                return_value=True,
            ),
            patch.dict("sys.modules", {"sentence_transformers": mock_st}),
        ):
            engine._load_model()

        assert engine._model is mock_model
        assert engine._available is True
        mock_st.SentenceTransformer.assert_called_once_with("test", device="cpu", backend="onnx")

    def test_onnx_skipped_without_optimum(self):
        """A bare onnxruntime install is not enough — the probe looks for optimum."""
        engine = EmbeddingEngine(model_name="test", use_onnx=True)
        mock_model = MagicMock()
        mock_st = MagicMock()
        mock_st.SentenceTransformer.return_value = mock_model

        with (
            patch(
                "graph_mem.semantic.embeddings._onnx_backend_available",
                return_value=False,
            ),
            patch.dict("sys.modules", {"sentence_transformers": mock_st}),
        ):
            engine._load_model()

        # Straight to the PyTorch call: no backend= kwarg anywhere.
        mock_st.SentenceTransformer.assert_called_once_with("test", device="cpu")

    def test_onnx_probe_false_when_optimum_missing(self):
        """The probe reports what it claims: optimum.onnxruntime importability."""
        with patch("importlib.util.find_spec", side_effect=ModuleNotFoundError("optimum")):
            assert _onnx_backend_available() is False
        with patch("importlib.util.find_spec", return_value=None):
            assert _onnx_backend_available() is False
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            assert _onnx_backend_available() is True


# ===========================================================================
# 6. _load_model ONNX fails, PyTorch succeeds
# ===========================================================================


class TestLoadModelOnnxFallback:
    def test_onnx_fails_pytorch_succeeds(self):
        engine = EmbeddingEngine(model_name="test", use_onnx=True)
        mock_model = MagicMock()
        mock_st = MagicMock()

        # First call (ONNX) raises, second call (PyTorch) succeeds
        mock_st.SentenceTransformer.side_effect = [
            RuntimeError("ONNX not available"),
            mock_model,
        ]

        with (
            patch(
                "graph_mem.semantic.embeddings._onnx_backend_available",
                return_value=True,
            ),
            patch.dict("sys.modules", {"sentence_transformers": mock_st}),
        ):
            engine._load_model()

        assert engine._model is mock_model
        assert engine._available is True
        assert mock_st.SentenceTransformer.call_count == 2


# ===========================================================================
# 7. _ensure_model_loaded thread safety
# ===========================================================================


class TestEnsureModelLoadedThreadSafety:
    def test_concurrent_calls_no_crash(self):
        """Multiple threads calling _ensure_model_loaded don't crash."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True

        load_count = 0
        lock = threading.Lock()

        def mock_load_model():
            nonlocal load_count
            with lock:
                load_count += 1
            engine._model = MagicMock()

        def mock_detect_dim():
            return 4

        engine._load_model = mock_load_model  # type: ignore[assignment]
        engine._detect_dimension = mock_detect_dim  # type: ignore[assignment]

        errors: list[Exception] = []

        def worker():
            try:
                engine._ensure_model_loaded()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert engine._model_loaded is True
        assert engine._dimension == 4
        # Lock ensures only one thread actually loads
        assert load_count == 1

    def test_double_check_after_lock(self):
        """Second check inside lock skips load when another thread already loaded (line 209)."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True  # Already loaded by "another thread"

        call_count = 0

        def counting_load():
            nonlocal call_count
            call_count += 1

        engine._load_model = counting_load  # type: ignore[assignment]
        engine._ensure_model_loaded()
        assert call_count == 0


# ===========================================================================
# 8. embed with empty input
# ===========================================================================


class TestEmbedEmpty:
    async def test_embed_empty_list(self):
        """embed([]) returns [] without touching model."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4
        engine._model = MagicMock()

        storage = _make_storage()
        engine._storage = storage

        result = await engine.embed([], storage)
        assert result == []


# ===========================================================================
# 9. embed when not available
# ===========================================================================


class TestEmbedUnavailable:
    async def test_embed_raises_when_unavailable(self):
        engine = EmbeddingEngine()
        assert engine.available is False
        with pytest.raises(EmbeddingError, match="not available"):
            await engine.embed(["hello"])


# ===========================================================================
# 10. initialize — reads metadata from storage
# ===========================================================================


class TestInitialize:
    async def test_initialize_reads_stored_dimension(self):
        """initialize() reads stored dimension and creates vec tables."""
        engine = EmbeddingEngine(model_name="test")
        storage = _make_storage()
        storage.get_metadata = AsyncMock(
            side_effect=lambda key: "384" if key == "embedding_dimension" else None
        )

        await engine.initialize(storage)

        assert engine._stored_dimension == 384
        assert engine._dimension == 384
        assert engine._available is True
        storage.ensure_vec_tables.assert_awaited_once_with(384)

    async def test_initialize_no_stored_dimension(self):
        """initialize() with no stored dim still marks available."""
        engine = EmbeddingEngine(model_name="test")
        storage = _make_storage()

        await engine.initialize(storage)

        assert engine._stored_dimension is None
        assert engine._available is True
        storage.ensure_vec_tables.assert_not_awaited()

    async def test_initialize_sqlite_error_disables(self):
        """initialize() catches sqlite3.Error and disables engine."""
        engine = EmbeddingEngine(model_name="test")
        storage = _make_storage()
        storage.get_metadata = AsyncMock(side_effect=sqlite3.OperationalError("db locked"))

        await engine.initialize(storage)

        assert engine._available is False

    async def test_initialize_value_error_disables(self):
        """initialize() catches ValueError and disables engine."""
        engine = EmbeddingEngine(model_name="test")
        storage = _make_storage()
        storage.get_metadata = AsyncMock(return_value="not-a-number")

        # int("not-a-number") raises ValueError
        await engine.initialize(storage)

        assert engine._available is False

    async def test_set_storage_and_resolve(self):
        """set_storage stores backend; _resolve_storage uses it."""
        engine = EmbeddingEngine()
        storage = _make_storage()
        engine.set_storage(storage)
        assert engine._resolve_storage() is storage

    async def test_resolve_storage_explicit_overrides(self):
        """Explicit storage param takes precedence."""
        engine = EmbeddingEngine()
        s1 = _make_storage()
        s2 = _make_storage()
        engine.set_storage(s1)
        assert engine._resolve_storage(s2) is s2

    async def test_resolve_storage_no_storage_raises(self):
        """_resolve_storage raises when none available (line 115)."""
        engine = EmbeddingEngine()
        with pytest.raises(EmbeddingError, match="No storage available"):
            engine._resolve_storage()


# ===========================================================================
# 11. _embedding_to_bytes / _bytes_to_embedding roundtrip
# ===========================================================================


class TestSerializationRoundtrip:
    def test_roundtrip_list(self):
        original = [1.0, -2.5, 3.14, 0.0]
        blob = _embedding_to_bytes(original)
        restored = _bytes_to_embedding(blob)
        assert len(restored) == 4
        for a, b in zip(original, restored, strict=True):
            assert abs(a - b) < 1e-5

    def test_roundtrip_numpy(self):
        arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        blob = _embedding_to_bytes(arr)
        restored = _bytes_to_embedding(blob)
        assert len(restored) == 3
        for a, b in zip(arr.tolist(), restored, strict=True):
            assert abs(a - b) < 1e-6

    def test_empty_roundtrip(self):
        blob = _embedding_to_bytes([])
        restored = _bytes_to_embedding(blob)
        assert restored == []


# ===========================================================================
# 12-15. Delegation methods
# ===========================================================================


class TestDelegationMethods:
    async def test_upsert_entity_embedding(self):
        engine = EmbeddingEngine()
        storage = _make_storage()
        engine.set_storage(storage)

        await engine.upsert_entity_embedding("ent-1", [1.0, 2.0])

        storage.upsert_entity_embedding.assert_awaited_once()
        call_args = storage.upsert_entity_embedding.call_args
        assert call_args[0][0] == "ent-1"
        # Second arg is bytes blob
        assert isinstance(call_args[0][1], bytes)

    async def test_delete_entity_embedding(self):
        engine = EmbeddingEngine()
        storage = _make_storage()
        engine.set_storage(storage)

        await engine.delete_entity_embedding("ent-1")

        storage.delete_entity_embedding.assert_awaited_once_with("ent-1")

    async def test_upsert_observation_embedding(self):
        engine = EmbeddingEngine()
        storage = _make_storage()
        engine.set_storage(storage)

        await engine.upsert_observation_embedding("obs-1", [0.5, 0.5])

        storage.upsert_observation_embedding.assert_awaited_once()
        assert storage.upsert_observation_embedding.call_args[0][0] == "obs-1"

    async def test_delete_observation_embedding(self):
        engine = EmbeddingEngine()
        storage = _make_storage()
        engine.set_storage(storage)

        await engine.delete_observation_embedding("obs-1")

        storage.delete_observation_embedding.assert_awaited_once_with("obs-1")


# ===========================================================================
# Extra: _detect_dimension (lines 181-187)
# ===========================================================================


class TestDetectDimension:
    def test_detect_dimension_calls_model(self):
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4]], dtype=np.float32)
        engine._model = mock_model

        # _detect_dimension calls _load_model first — stub it
        engine._load_model = lambda: None  # type: ignore[assignment]

        dim = engine._detect_dimension()
        assert dim == 4
        mock_model.encode.assert_called_once()

    def test_detect_dimension_no_model_raises(self):
        """_detect_dimension raises when _model is None after _load_model."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._load_model = lambda: None  # type: ignore[assignment]
        # _model stays None

        with pytest.raises(EmbeddingError, match="Model failed to load"):
            engine._detect_dimension()


# ===========================================================================
# Extra: embed full path (lines 280-335)
# ===========================================================================


class TestEmbedFullPath:
    async def test_embed_computes_uncached_and_caches(self):
        """embed() computes vectors for uncached texts and stores them."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array(
            [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]], dtype=np.float32
        )
        engine._model = mock_model

        storage = _make_storage()
        engine._storage = storage

        result = await engine.embed(["hello", "world"], storage)

        assert len(result) == 2
        assert result[0] is not None
        assert len(result[0]) == 4
        assert result[1] is not None
        assert len(result[1]) == 4

        # Both vectors cached by a single batched INSERT
        inserts = _statements_starting(storage, "INSERT")
        assert len(inserts) == 1
        assert inserts[0].count("(?, ?, ?, ?)") == 2

    async def test_embed_uses_cache_hit(self):
        """embed() returns cached embedding without calling model."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4

        mock_model = MagicMock()
        engine._model = mock_model

        cached_blob = _embedding_to_bytes([1.0, 2.0, 3.0, 4.0])
        storage = _make_storage({_content_hash("cached text"): cached_blob})
        engine._storage = storage

        result = await engine.embed(["cached text"], storage)

        assert len(result) == 1
        assert abs(result[0][0] - 1.0) < 1e-6
        # Model should NOT be called since all texts were cached
        mock_model.encode.assert_not_called()

    async def test_embed_batches_cache_lookups_into_one_query(self):
        """A batch of texts costs one SELECT, not one per text."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4

        texts = [f"text {i}" for i in range(20)]
        mock_model = MagicMock()
        mock_model.encode.return_value = np.zeros((20, 4), dtype=np.float32)
        engine._model = mock_model

        storage = _make_storage()
        await engine.embed(texts, storage)

        assert len(_statements_starting(storage, "SELECT")) == 1
        assert len(_statements_starting(storage, "INSERT")) == 1

    async def test_embed_runs_encode_off_the_event_loop(self):
        """model.encode() must not run on the event loop thread."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4

        seen: list[threading.Thread] = []

        def encode(texts, **kwargs):
            seen.append(threading.current_thread())
            return np.zeros((len(texts), 4), dtype=np.float32)

        mock_model = MagicMock()
        mock_model.encode.side_effect = encode
        engine._model = mock_model

        await engine.embed(["a", "b"], _make_storage())

        assert seen and seen[0] is not threading.main_thread()

    async def test_cache_hit_refreshes_last_use(self):
        """A hit updates created_at, which is what makes eviction LRU."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4
        engine._model = MagicMock()

        storage = _make_storage({_content_hash("warm"): _embedding_to_bytes([1.0, 0.0, 0.0, 0.0])})
        await engine.embed(["warm"], storage)

        updates = _statements_starting(storage, "UPDATE")
        assert len(updates) == 1
        assert "SET created_at = ?" in updates[0]

    async def test_prune_is_amortized(self):
        """Pruning happens every _PRUNE_EVERY_N_BATCHES batches, not every batch."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False, cache_size=5)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4

        mock_model = MagicMock()
        mock_model.encode.return_value = np.zeros((1, 4), dtype=np.float32)
        engine._model = mock_model

        storage = _make_storage()
        for i in range(_PRUNE_EVERY_N_BATCHES - 1):
            await engine.embed([f"text {i}"], storage)
        storage.prune_embedding_cache.assert_not_awaited()

        await engine.embed(["last text"], storage)
        storage.prune_embedding_cache.assert_awaited_once_with(5)

    async def test_embed_rejects_short_model_output(self):
        """A model returning fewer vectors than texts raises instead of returning None."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4

        mock_model = MagicMock()
        mock_model.encode.return_value = np.zeros((1, 4), dtype=np.float32)
        engine._model = mock_model

        with pytest.raises(EmbeddingError, match="Model returned 1 vectors for 2 texts"):
            await engine.embed(["a", "b"], _make_storage())

    async def test_embed_first_session_persists_metadata(self):
        """When _stored_dimension is None, embed persists metadata."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = None  # First session

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4]], dtype=np.float32)
        engine._model = mock_model

        storage = _make_storage()
        engine._storage = storage

        await engine.embed(["test"], storage)

        # Should have persisted dimension and model name
        storage.set_metadata.assert_any_await("embedding_dimension", "4")
        storage.set_metadata.assert_any_await("embedding_model", "test")
        storage.ensure_vec_tables.assert_awaited_once_with(4)
        assert engine._stored_dimension == 4

    async def test_embed_model_none_after_load_raises(self):
        """embed() raises if _model is None after _load_model."""
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4
        engine._model = None  # Somehow None

        storage = _make_storage()
        engine._storage = storage

        # _load_model won't set _model
        engine._load_model = lambda: None  # type: ignore[assignment]

        with pytest.raises(EmbeddingError, match="Model failed to load"):
            await engine.embed(["test"], storage)


# ===========================================================================
# Extra: dimension property (line 125)
# ===========================================================================


class TestModelSwapDetection:
    async def test_initialize_warns_when_model_changed(self, caplog):
        """A different stored model name is reported, loudly, and left alone."""
        engine = EmbeddingEngine(model_name="new-model")
        storage = _make_storage()
        storage.get_metadata = AsyncMock(
            side_effect=lambda key: {
                "embedding_dimension": "384",
                "embedding_model": "old-model",
            }.get(key)
        )

        with caplog.at_level(logging.WARNING, logger="graph_mem.semantic.embeddings"):
            await engine.initialize(storage)

        assert engine.vectors_stale is True
        assert engine.stored_model_name == "old-model"
        message = caplog.text
        assert "old-model" in message and "new-model" in message
        assert "stale" in message.lower()
        # Nothing destructive happened.
        storage.delete_entity_embedding.assert_not_awaited()
        storage.delete_observation_embedding.assert_not_awaited()

    async def test_initialize_quiet_when_model_matches(self, caplog):
        engine = EmbeddingEngine(model_name="same-model")
        storage = _make_storage()
        storage.get_metadata = AsyncMock(
            side_effect=lambda key: {
                "embedding_dimension": "384",
                "embedding_model": "same-model",
            }.get(key)
        )

        with caplog.at_level(logging.WARNING, logger="graph_mem.semantic.embeddings"):
            await engine.initialize(storage)

        assert engine.vectors_stale is False
        assert caplog.text == ""

    async def test_vectors_stale_false_without_stored_model(self):
        """A database that never recorded a model is not 'stale', just new."""
        engine = EmbeddingEngine(model_name="anything")
        await engine.initialize(_make_storage())
        assert engine.vectors_stale is False


# ===========================================================================
# Embedding cache schema (migration v002) — two models must coexist
# ===========================================================================


class TestEmbeddingCacheSchema:
    async def test_composite_key_lets_two_models_coexist(self, tmp_path):
        """Same text, two models: both rows survive and both read back."""
        backend = SQLiteBackend(tmp_path / "cache.db")
        await backend.initialize()
        try:
            blob_a = _embedding_to_bytes([1.0, 0.0])
            blob_b = _embedding_to_bytes([0.0, 1.0])
            h = _content_hash("shared text")
            await backend.set_cached_embedding(h, blob_a, "model-a", 1.0)
            await backend.set_cached_embedding(h, blob_b, "model-b", 2.0)

            assert await backend.get_cached_embedding(h, "model-a") == blob_a
            assert await backend.get_cached_embedding(h, "model-b") == blob_b
        finally:
            await backend.close()

    async def test_eviction_is_lru_not_fifo(self, tmp_path):
        """A re-used entry outlives a newer unused one, as the docs promise."""
        backend = SQLiteBackend(tmp_path / "lru.db")
        await backend.initialize()
        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        engine._available = True
        engine._model_loaded = True
        engine._dimension = 4
        engine._stored_dimension = 4
        mock_model = MagicMock()
        mock_model.encode.return_value = np.zeros((1, 4), dtype=np.float32)
        engine._model = mock_model

        try:
            with patch(
                "graph_mem.semantic.embeddings.time.time", side_effect=[100.0, 200.0, 300.0]
            ):
                await engine.embed(["old but used"], backend)  # written at t=100
                await engine.embed(["newer but idle"], backend)  # written at t=200
                await engine.embed(["old but used"], backend)  # cache hit, touched to t=300

            await backend.prune_embedding_cache(1)
            rows = await backend.fetch_all("SELECT created_at FROM embedding_cache")
            assert [r["created_at"] for r in rows] == [300.0]
        finally:
            await backend.close()

    async def test_migration_preserves_rows_and_is_idempotent(self, tmp_path):
        """v002 rebuilds the table without losing v001's cached vectors."""
        db = Database(tmp_path / "migrate.db")
        await db.initialize()
        try:
            await v001_initial.migrate(db)
            await db.execute(
                "INSERT INTO embedding_cache (content_hash, embedding, model_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("hash-1", _embedding_to_bytes([0.5, 0.5]), "model-a", 123.0),
            )

            await v002_embedding_cache_pk.migrate(db)
            # Second run must be a no-op, not a failure or a data loss.
            await v002_embedding_cache_pk.migrate(db)

            rows = await db.fetch_all("SELECT * FROM embedding_cache")
            assert len(rows) == 1
            assert rows[0]["content_hash"] == "hash-1"
            assert rows[0]["model_name"] == "model-a"
            assert rows[0]["created_at"] == 123.0

            pk = [
                r["name"]
                for r in await db.fetch_all("PRAGMA table_info(embedding_cache)")
                if r["pk"]
            ]
            assert sorted(pk) == ["content_hash", "model_name"]
        finally:
            await db.close()


class TestDimensionProperty:
    def test_dimension_raises_when_none(self):
        engine = EmbeddingEngine()
        with pytest.raises(EmbeddingError, match="not initialized"):
            _ = engine.dimension

    def test_dimension_returns_value(self):
        engine = EmbeddingEngine()
        engine._dimension = 384
        assert engine.dimension == 384
