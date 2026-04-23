"""Optional dense embeddings layer.

Lazy-loaded. Dense retrieval now reranks an FTS candidate pool instead of
running an independent whole-corpus nearest-neighbor search.
"""

from __future__ import annotations

import contextlib
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from repopulse.logging import get_logger

log = get_logger("dense")


@dataclass
class DenseResult:
    chunk_id: int
    distance: float


class DenseBackend:
    """Wrapper over fastembed + sqlite-vec.

    Dense retrieval is a best-effort augmentation, not the primary retrieval
    path. When the backend cannot initialize, it logs the exact failure and
    stays disabled for the session rather than crashing the server.
    """

    def __init__(self, conn: Any, model_name: str, batch_size: int = 32) -> None:
        self.conn = conn
        self.model_name = model_name
        self.batch_size = batch_size
        self._embedder: Any = None
        self._dim: int | None = None
        self._available = False
        self._ensure_available()

    def _ensure_available(self) -> None:
        try:
            import sqlite_vec
        except ImportError as exc:
            log.warning("dense_sqlite_vec_missing", error=str(exc))
            return
        try:
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
        except (AttributeError, OSError, sqlite3.Error) as exc:
            log.warning("dense_sqlite_vec_init_failed", error=str(exc))
            return
        finally:
            with contextlib.suppress(AttributeError, sqlite3.Error):
                self.conn.enable_load_extension(False)
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            log.warning("dense_fastembed_missing", error=str(exc))
            return
        try:
            self._embedder = TextEmbedding(model_name=self.model_name)
            dim = len(next(iter(self._embedder.embed(["probe"]))))
        except (OSError, RuntimeError, StopIteration, TypeError, ValueError) as exc:
            log.warning("dense_embedder_init_failed", error=str(exc), model=self.model_name)
            return
        if not isinstance(dim, int) or dim <= 0 or dim > 8192:
            log.warning("dense_invalid_embedding_dim", dim=dim, model=self.model_name)
            return
        self._dim = dim
        try:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_vector_cache (
                  chunk_id   INTEGER PRIMARY KEY,
                  embedding  BLOB NOT NULL
                )
                """
            )
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors "
                f"USING vec0(chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{dim}])"
            )
        except sqlite3.Error as exc:
            log.warning("dense_schema_init_failed", error=str(exc))
            return
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dim(self) -> int | None:
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self._available or self._embedder is None:
            return [[] for _ in texts]
        result: list[list[float]] = []
        try:
            for vec in self._embedder.embed(texts):
                result.append([float(x) for x in vec])
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning("dense_embed_texts_failed", error=str(exc), text_count=len(texts))
            return [[] for _ in texts]
        return result

    def ensure_embeddings(self, items: list[tuple[int, str]]) -> int:
        if not self._available or not items:
            return 0
        ordered_items: list[tuple[int, str]] = []
        seen: set[int] = set()
        for chunk_id, text in items:
            if chunk_id in seen or not text:
                continue
            ordered_items.append((chunk_id, text))
            seen.add(chunk_id)
        if not ordered_items:
            return 0
        try:
            missing_ids = self._missing_chunk_ids([chunk_id for chunk_id, _ in ordered_items])
        except sqlite3.Error as exc:
            log.warning("dense_missing_id_lookup_failed", error=str(exc))
            return 0
        if not missing_ids:
            return 0
        text_by_id = {chunk_id: text for chunk_id, text in ordered_items}
        texts = [text_by_id[chunk_id] for chunk_id in missing_ids]
        vectors = self.embed_texts(texts)
        pairs = [
            (chunk_id, vec)
            for chunk_id, vec in zip(missing_ids, vectors, strict=True)
            if vec
        ]
        if pairs:
            self.upsert(pairs)
        return len(pairs)

    def upsert(self, items: list[tuple[int, list[float]]]) -> None:
        if not self._available or not items:
            return
        cache_rows = [(chunk_id, _to_blob(vec)) for chunk_id, vec in items]
        try:
            self.conn.executemany(
                "INSERT OR REPLACE INTO chunk_vector_cache(chunk_id, embedding) VALUES (?, ?)",
                cache_rows,
            )
            for chunk_id, blob in cache_rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO chunk_vectors(chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, blob),
                )
        except sqlite3.Error as exc:
            log.warning("dense_upsert_failed", error=str(exc), row_count=len(cache_rows))

    def delete(self, chunk_ids: list[int]) -> None:
        if not self._available or not chunk_ids:
            return
        placeholders = ",".join("?" for _ in chunk_ids)
        try:
            self.conn.execute(
                f"DELETE FROM chunk_vector_cache WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            self.conn.execute(
                f"DELETE FROM chunk_vectors WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
        except sqlite3.Error as exc:
            log.warning("dense_delete_failed", error=str(exc), row_count=len(chunk_ids))

    def search(
        self, query_text: str, candidate_chunk_ids: list[int], limit: int = 50
    ) -> list[DenseResult]:
        if not self._available or not candidate_chunk_ids:
            return []
        vecs = self.embed_texts([query_text])
        if not vecs or not vecs[0]:
            return []
        query_vec = vecs[0]
        try:
            stored = self._fetch_embeddings(candidate_chunk_ids)
        except sqlite3.Error as exc:
            log.warning("dense_fetch_failed", error=str(exc), row_count=len(candidate_chunk_ids))
            return []
        results: list[DenseResult] = []
        for chunk_id in candidate_chunk_ids:
            vec = stored.get(chunk_id)
            if not vec:
                continue
            results.append(
                DenseResult(
                    chunk_id=chunk_id,
                    distance=_cosine_distance(query_vec, vec),
                )
            )
        results.sort(key=lambda row: row.distance)
        return results[:limit]

    def _missing_chunk_ids(self, chunk_ids: list[int]) -> list[int]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.conn.execute(
            f"SELECT chunk_id FROM chunk_vector_cache WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        existing = {int(row["chunk_id"]) for row in rows}
        return [chunk_id for chunk_id in chunk_ids if chunk_id not in existing]

    def _fetch_embeddings(self, chunk_ids: list[int]) -> dict[int, list[float]]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.conn.execute(
            f"SELECT chunk_id, embedding FROM chunk_vector_cache WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        embeddings: dict[int, list[float]] = {}
        for row in rows:
            vec = _from_blob(row["embedding"])
            if vec:
                embeddings[int(row["chunk_id"])] = vec
        return embeddings


def _to_blob(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"{len(vec)}f", *vec)


def _from_blob(raw: Any) -> list[float]:
    import struct

    if isinstance(raw, memoryview):
        data = raw.tobytes()
    elif isinstance(raw, bytearray):
        data = bytes(raw)
    elif isinstance(raw, bytes):
        data = raw
    else:
        return []
    if not data or len(data) % 4 != 0:
        return []
    return [float(x) for x in struct.unpack(f"{len(data) // 4}f", data)]


def _cosine_distance(lhs: list[float], rhs: list[float]) -> float:
    if not lhs or not rhs or len(lhs) != len(rhs):
        return 1.0
    dot = sum(a * b for a, b in zip(lhs, rhs, strict=True))
    lhs_norm = math.sqrt(sum(a * a for a in lhs))
    rhs_norm = math.sqrt(sum(b * b for b in rhs))
    if lhs_norm == 0.0 or rhs_norm == 0.0:
        return 1.0
    cosine = max(-1.0, min(1.0, dot / (lhs_norm * rhs_norm)))
    return 1.0 - cosine
